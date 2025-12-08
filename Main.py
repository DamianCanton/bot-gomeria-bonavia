import os
import threading
import logging
import requests
import re
from bs4 import BeautifulSoup
from flask import Flask

# Imports de Telegram
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# --- 1. CONFIGURACIÓN ---
TOKEN = '7890020254:AAH8Arv39q57dIdWC0zYN4qpWvijSN2LMcE' # Tu token
PORT = int(os.environ.get('PORT', 10000))

# Reglas de Negocio
MARCAS_VIP = ["dunlop", "fate", "corven"]
DESCUENTO_VIP = 0.05
DESCUENTO_GENERAL = 0.10
MARGEN_GANANCIA = 1.25 
MAX_OPCIONES = 6 # Aumenté uno extra por si el filtro elimina alguno

# Configuración de Logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- 2. LÓGICA DE NEGOCIO (EL CEREBRO) ---

def formatear_precio(valor):
    """
    Formatea un número float a string con formato moneda: $1.234,56
    """
    return f"${valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def filtrar_por_rodado(query_usuario, lista_productos):
    """
    Filtra una lista de neumáticos asegurando que coincidan con el rodado (R)
    que el usuario pidió en su búsqueda (ej: '175 65 15').
    """
    # 1. IDENTIFICAR EL RODADO OBJETIVO EN LA BÚSQUEDA
    # Buscamos el último número de 2 dígitos en la query o "R14"
    match_objetivo = re.search(r'(?:R|r)?(\d{2})$', query_usuario.strip())
    
    if not match_objetivo:
        return lista_productos # Si no detectamos rodado, devolvemos todo
    
    rodado_objetivo = match_objetivo.group(1) # Ej: "15"
    productos_filtrados = []
    
    # 2. INSPECCIONAR CADA PRODUCTO
    for prod in lista_productos:
        # Extraemos el rodado del título del producto (ej: "175/65 R14")
        match_producto = re.search(r'[R|r](\d{2})', prod['titulo'])
        
        if match_producto:
            rodado_producto = match_producto.group(1)
            # 3. LA COMPUERTA LÓGICA: ¿Coinciden?
            if rodado_producto == rodado_objetivo:
                productos_filtrados.append(prod)
            else:
                # Log para ver qué descartamos (opcional, solo sale en consola de Render)
                print(f"🗑️ Descartado {prod['titulo']} (Es R{rodado_producto}, buscaban R{rodado_objetivo})")
        else:
            # Si el título no dice el rodado, lo dejamos pasar por seguridad
            productos_filtrados.append(prod)
            
    return productos_filtrados

def cotizar_producto_individual(url):
    """ Entra a un link y saca la data precisa """
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        texto = soup.get_text(" ", strip=True)
        
        # Filtro Estricto: Busca precio pegado a "con Transferencia"
        match = re.search(r'(\$\s?[\d\.]+,\d{2})\s+con\s+Transferencia', texto, re.IGNORECASE)
        
        if match:
            precio_str = match.group(1).replace('$','').strip().replace('.','').replace(',','.')
            precio_raw = float(precio_str)
            
            h1 = soup.find('h1')
            titulo = h1.get_text().strip() if h1 else "Producto sin nombre"
            
            titulo_lower = titulo.lower()
            es_vip = any(m in titulo_lower for m in MARCAS_VIP)
            desc = DESCUENTO_VIP if es_vip else DESCUENTO_GENERAL
            
            costo = precio_raw * (1 - desc)
            venta = costo * MARGEN_GANANCIA
            
            return {
                "titulo": titulo,
                "precio_web": precio_raw,
                "costo": costo,
                "venta": venta,
                "vip": es_vip
            }
        return None
    except Exception as e:
        print(f"Error cotizando {url}: {e}")
        return None

def buscar_multiples_opciones(medida):
    """ Busca en el catálogo, FILTRA POR RODADO y devuelve mensajes """
    query = medida.replace(" ", "%20")
    url_busqueda = f"https://www.gomeriacentral.com/search/?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    productos = []
    urls_vistas = set()
    
    try:
        resp = requests.get(url_busqueda, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        links = soup.find_all('a', href=True)
        partes = [p for p in medida.split() if p.isdigit()]
        
        for link in links:
            if len(productos) >= MAX_OPCIONES: break
            
            txt = link.get_text(" ", strip=True).lower()
            href = link['href']
            
            if ("/productos/" in href or "/neumaticos/" in href) and all(p in txt for p in partes):
                full_url = href if href.startswith("http") else "https://www.gomeriacentral.com" + href
                
                if full_url in urls_vistas: continue
                urls_vistas.add(full_url)
                
                dato = cotizar_producto_individual(full_url)
                if dato: productos.append(dato)
        
        # --- AQUÍ APLICAMOS TU NUEVO FILTRO ---
        if productos:
            print(f"🔎 Antes del filtro: {len(productos)} productos.")
            productos = filtrar_por_rodado(medida, productos)
            print(f"✅ Después del filtro: {len(productos)} productos.")

        if not productos: 
            return None, "❌ No encontré precios exactos para esa medida. Revisá el rodado."
            
        # Ordenamos: más barato primero
        productos.sort(key=lambda x: x['venta'])
        
        # --- GENERACIÓN DE MENSAJES ---
        
        # 1. Reporte Interno
        msg_interno = f"🕵️‍♂️ REPORTE PRIVADO: {medida}\n"
        msg_interno += f"(Costo Real vs Ganancia Neta)\n\n"
        
        for i, p in enumerate(productos, 1):
            icon = "⭐" if p['vip'] else "🔹"
            ganancia = p['venta'] - p['costo']
            msg_interno += (f"{i}. {icon} {p['titulo']}\n"
                            f"   📉 Costo: {formatear_precio(p['costo'])} | 💰 Gana: {formatear_precio(ganancia)}\n"
                            f"   🏷️ Venta: {formatear_precio(p['venta'])}\n\n")
            
        # 2. Cotización Cliente
        msg_cliente = f"👋 Hola! Te paso las opciones para {medida}:\n\n"
        
        for p in productos:
            msg_cliente += f"🔘 {p['titulo']}\n"
            msg_cliente += f"   💲 Precio Final: {formatear_precio(p['venta'])}\n\n"
            
        msg_cliente += "✅ Precios contado/transferencia.\n"

        return msg_interno, msg_cliente

    except Exception as e: 
        return None, f"Error general: {str(e)}"

# --- 3. TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        ["175 65 14", "185 60 15"],
        ["195 55 16", "205 55 16"],
        ["175 70 13", "165 70 13"]
    ]
    markup = ReplyKeyboardMarkup(teclado, one_time_keyboard=False, resize_keyboard=True)
    
    await update.message.reply_text(
        "👋 ¡Hola Jefe! Toca un botón o escribí la medida a mano.",
        reply_markup=markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    
    # Filtro básico de entrada
    numeros = [x for x in texto.split() if x.isdigit()]
    if len(numeros) < 2:
        await update.message.reply_text(
            "⚠️ Falta información. Escribí la medida completa (Ej: 175 70 13).",
            parse_mode='Markdown'
        )
        return

    await update.message.reply_text(f"🔎 Buscando variantes para '{texto}'...")
    
    msg_interno, msg_cliente = buscar_multiples_opciones(texto)
    
    if msg_interno:
        await update.message.reply_text(msg_interno, parse_mode='Markdown')
        if msg_cliente:
            await update.message.reply_text("👇 PARA REENVIAR 👇", parse_mode='Markdown')
            await update.message.reply_text(msg_cliente, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg_cliente)

# --- 4. SERVIDOR WEB FALSO ---
app = Flask(__name__)

@app.route('/')
def index():
    return "🤖 Gomería Bot v1.4 (Con Filtro Rodado) - OPERATIVO 🟢"

def run_flask():
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)

# --- 5. ARRANQUE ---
if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🚀 Web iniciada. Arrancando Bot...")

    if not TOKEN:
        print("❌ ERROR: No encontré el TELEGRAM_TOKEN.")
    else:
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("🤖 Bot escuchando...")
        application.run_polling()