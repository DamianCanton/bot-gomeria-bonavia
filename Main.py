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
# Token: Render lo inyecta como variable de entorno
TOKEN = '8222498081:AAEuL85-S8k4fe2sTYeCR4a4ZULl7AVBDGA'
PORT = int(os.environ.get('PORT', 10000))

# Reglas de Negocio
MARCAS_VIP = ["dunlop", "fate", "corven"]
DESCUENTO_VIP = 0.05
DESCUENTO_GENERAL = 0.10
MARGEN_GANANCIA = 1.25 # Margen del 25% para tener colchón
MAX_OPCIONES = 5

# Configuración de Logs (Para ver errores en Render)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- 2. LÓGICA DE NEGOCIO (EL CEREBRO) ---

def formatear_precio(valor):
    """ Formatea números a moneda ($ 1.234) """
    if valor is None: return "$ --"
    return f"${valor:,.0f}".replace(",", ".")

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
            # Limpieza de número ($1.000,00 -> 1000.0)
            precio_str = match.group(1).replace('$','').strip().replace('.','').replace(',','.')
            precio_raw = float(precio_str)
            
            h1 = soup.find('h1')
            titulo = h1.get_text().strip() if h1 else "Producto sin nombre"
            
            # Cálculo de Costos
            titulo_lower = titulo.lower()
            es_vip = any(m in titulo_lower for m in MARCAS_VIP)
            desc = DESCUENTO_VIP if es_vip else DESCUENTO_GENERAL
            
            costo = precio_raw * (1 - desc)
            venta = costo * MARGEN_GANANCIA
            
            # --- Detección de Stock (MEJORADA) ---
            stock = -1  # Por defecto: No sabemos
            
            # Búsqueda más precisa de estados de agotamiento
            # Usamos \b (word boundary) para buscar palabras completas, no parciales
            texto_lower = texto.lower()
            
            # Patrones de producto agotado (solo frases específicas)
            patrones_agotado = [
                r'\bagotado\b',
                r'\bsin\s+stock\b',
                r'\bno\s+(?:hay|tiene|disponible|queda)\s+stock\b',
                r'\bno\s+disponible\b',
                r'\bsin\s+unidades\b',
                r'\bno\s+hay\s+unidades\b',
                r'\bstock:\s*0\b',
                r'\bdisponibilidad:\s*no\b'
            ]
            
            # Si encontramos algún patrón de agotado
            if any(re.search(patron, texto_lower) for patron in patrones_agotado):
                stock = 0
            else:
                # Intenta buscar "X unidades disponibles" o "Stock: X"
                stock_match = re.search(r'(\d+)\s+unidades?\s+disponibles?', texto_lower)
                if not stock_match:
                    stock_match = re.search(r'stock:\s*(\d+)', texto_lower)
                if not stock_match:
                    stock_match = re.search(r'disponibles?:\s*(\d+)', texto_lower)
                    
                if stock_match:
                    stock = int(stock_match.group(1))

            return {
                "titulo": titulo,
                "precio_web": precio_raw,
                "costo": costo,
                "venta": venta,
                "vip": es_vip,
                "stock": stock
            }
        return None
    except Exception as e:
        print(f"Error cotizando {url}: {e}")
        return None

def buscar_multiples_opciones(medida):
    """ Busca en el catálogo y devuelve dos mensajes (Interno y Cliente) """
    query = medida.replace(" ", "%20")
    url_busqueda = f"https://www.gomeriacentral.com/search/?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    productos = []
    urls_vistas = set()
    
    try:
        resp = requests.get(url_busqueda, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        links = soup.find_all('a', href=True)
        # Filtramos solo los links que tengan los números de la medida
        partes = [p for p in medida.split() if p.isdigit()]
        
        for link in links:
            if len(productos) >= MAX_OPCIONES: break
            
            txt = link.get_text(" ", strip=True).lower()
            href = link['href']
            
            # Filtro de calidad del link
            if ("/productos/" in href or "/neumaticos/" in href) and all(p in txt for p in partes):
                full_url = href if href.startswith("http") else "https://www.gomeriacentral.com" + href
                
                if full_url in urls_vistas: continue
                urls_vistas.add(full_url)
                
                dato = cotizar_producto_individual(full_url)
                if dato: productos.append(dato)
        
        if not productos: 
            return None, "❌ No encontré precios. Probá otra medida."
            
        # Ordernamos: más barato primero
        productos.sort(key=lambda x: x['venta'])

        # --- GENERACIÓN DE MENSAJES ---

        # 1. Reporte Interno (DATA PURA PARA TU VIEJO)
        msg_interno = f"🕵️‍♂️ REPORTE PRIVADO: {medida}\n"
        msg_interno += f"(Stock Real | Costo vs Ganancia)\n\n"
        
        for i, p in enumerate(productos, 1):
            icon = "⭐" if p['vip'] else "🔹"
            ganancia = p['venta'] - p['costo']
            
            # --- Lógica de Visualización de Stock (SOLO INTERNO) ---
            txt_stock = ""
            if p['stock'] == 0:
                txt_stock = "⛔ AGOTADO (Oculto al cliente)"
            elif p['stock'] == -1:
                txt_stock = "❓ Stock sin dato (Revisar web)"
            elif p['stock'] < 4:
                txt_stock = f"⚠️ CRÍTICO: Quedan {p['stock']}"
            else:
                txt_stock = f"✅ Stock: {p['stock']}"

            msg_interno += (f"{i}. {icon} {p['titulo']}\n"
                            f"   {txt_stock}\n"  # <--- INFORMACIÓN PRIVADA
                            f"   📉 Costo: {formatear_precio(p['costo'])} | 💰 Gana: {formatear_precio(ganancia)}\n"
                            f"   🏷️ Venta: {formatear_precio(p['venta'])}\n\n")
            
        # 2. Cotización Cliente (VIDRIERA LIMPIA)
        msg_cliente = f"👋 Hola! Te paso las opciones para {medida}:\n\n"
        opciones_validas = 0

        for p in productos:
            # FILTRO: Si está agotado, no se lo mostramos al cliente para no quedar mal
            if p['stock'] == 0:
                continue 

            msg_cliente += f"🔘 {p['titulo']}\n"
            # ACÁ NO PONEMOS STOCK, solo precio
            msg_cliente += f"   💲 Precio Final: {formatear_precio(p['venta'])}\n\n"
            
            opciones_validas += 1
            
        msg_cliente += "✅ Precios contado/transferencia.\n"

        # Si todo estaba sin stock...
        if opciones_validas == 0:
            msg_cliente = "� Hola! Por el momento no tengo stock disponible en esa medida."

        return msg_interno, msg_cliente

    except Exception as e: 
        return None, f"Error general: {str(e)}"

# --- 3. TELEGRAM HANDLERS (EL CUERPO) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # MEJORA: Botones Rápidos para no escribir tanto
    teclado = [
        ["175 65 14", "185 60 15"],
        ["195 55 16", "205 55 16"],
        ["175 70 13", "165 70 13"] # Agregados los clásicos
    ]
    markup = ReplyKeyboardMarkup(teclado, one_time_keyboard=False, resize_keyboard=True)
    
    await update.message.reply_text(
        "👋 ¡Hola Jefe! Toca un botón o escribí la medida a mano.",
        reply_markup=markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    
    # --- MEJORA: FILTRO ANTI-BASURA ---
    # Contamos cuántos grupos de números hay en el mensaje
    numeros = [x for x in texto.split() if x.isdigit()]
    
    # Si tiene menos de 2 números (Ej: "Hola", "Bot", "Precio"), ignoramos.
    if len(numeros) < 2:
        await update.message.reply_text(
            "⚠️ Falta información.\nPor favor escribí la medida completa (Ej: 175 70 13).",
            parse_mode='Markdown'
        )
        return

    # Si pasa el filtro, buscamos
    await update.message.reply_text(f"🔎 Buscando variantes para '{texto}'...")
    
    msg_interno, msg_cliente = buscar_multiples_opciones(texto)
    
    if msg_interno:
        # Enviamos reporte privado
        await update.message.reply_text(msg_interno, parse_mode='Markdown')
        # Enviamos cotización limpia
        if msg_cliente:
            await update.message.reply_text("👇 PARA REENVIAR 👇", parse_mode='Markdown')
            await update.message.reply_text(msg_cliente, parse_mode='Markdown')
    else:
        # Mensaje de error si no encontró nada
        await update.message.reply_text(msg_cliente)

# --- 4. SERVIDOR WEB FALSO (PARA RENDER) ---
app = Flask(__name__)

@app.route('/')
def index():
    return "🤖 Gomería Bot v1.3 - OPERATIVO 🟢"

def run_flask():
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)

# --- 5. ARRANQUE DEL SISTEMA ---
if __name__ == '__main__':
    # 1. Web en hilo secundario (Daemon)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🚀 Web iniciada. Arrancando Bot...")

    # 2. Bot en hilo principal (Main Thread)
    # Esto evita el error "set_wakeup_fd"
    if not TOKEN:
        print("❌ ERROR: No encontré el TELEGRAM_TOKEN en las variables de entorno.")
    else:
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("🤖 Bot escuchando...")
        application.run_polling()