import os
import threading
from flask import Flask
import logging
import requests
from bs4 import BeautifulSoup
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- 1. CONFIGURACIÓN ---
# Render nos da el puerto por variable de entorno, o usamos 10000 por defecto
PORT = int(os.environ.get('PORT', 10000))
TOKEN = "7890020254:AAH8Arv39q57dIdWC0zYN4qpWvijSN2LMcE" # Leemos el token de la configuración de Render

# Reglas de Negocio
MARCAS_VIP = ["dunlop", "fate", "corven"]
DESCUENTO_VIP = 0.05
DESCUENTO_GENERAL = 0.10
MARGEN_GANANCIA = 1.20
MAX_OPCIONES = 5

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 2. EL CEREBRO DEL BOT (Tu lógica intacta) ---
def cotizar_producto_individual(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        texto = soup.get_text(" ", strip=True)
        match = re.search(r'(\$\s?[\d\.]+,\d{2})\s+con\s+Transferencia', texto, re.IGNORECASE)
        
        if match:
            precio_raw = float(match.group(1).replace('$','').strip().replace('.','').replace(',','.'))
            h1 = soup.find('h1')
            titulo = h1.get_text().strip() if h1 else "Producto"
            es_vip = any(m in titulo.lower() for m in MARCAS_VIP)
            desc = DESCUENTO_VIP if es_vip else DESCUENTO_GENERAL
            costo = precio_raw * (1 - desc)
            venta = costo * MARGEN_GANANCIA
            return {"titulo": titulo, "costo": costo, "venta": venta, "vip": es_vip}
        return None
    except: return None

def buscar_multiples_opciones(medida):
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
        
        if not productos: return "❌ No encontré precios visibles para esa medida."
            
        productos.sort(key=lambda x: x['venta'])
        msg = f"📊 *MENÚ DE OPCIONES: {medida}*\n\n"
        for i, p in enumerate(productos, 1):
            icon = "⭐" if p['vip'] else "🔹"
            msg += f"{i}. {icon} *{p['titulo']}*\n   Costo: ${p['costo']:,.0f} | *Venta: ${p['venta']:,.0f}*\n\n"
        return msg + "💡 *Precios con +20% ganancia.*"
    except Exception as e: return f"Error: {str(e)}"

# --- 3. TELEGRAM SETUP ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Bot Activo! Pasame la medida.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    medida = update.message.text
    await update.message.reply_text(f"🔎 Buscando '{medida}'...")
    resultado = buscar_multiples_opciones(medida)
    await update.message.reply_text(resultado, parse_mode='Markdown')

def run_bot():
    """ Función que corre el bot en bucle infinito """
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.run_polling()

## --- 4. LA FACHADA (El Servidor Web con HTML Básico) ---
app = Flask(__name__)

@app.route('/')
def index():
    # AQUÍ ESTÁ TU HTML BÁSICO
    # No hace falta un archivo separado, lo escribimos directo como texto.
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gomería Bot</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f0f0f0; }
            h1 { color: #0088cc; }
            .status { font-size: 20px; color: green; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>🤖 Gomería Bot System</h1>
        <p>Estado del sistema: <span class="status">OPERATIVO 🟢</span></p>
        <p>Este servicio trabaja en segundo plano atendiendo consultas de Telegram.</p>
    </body>
    </html>
    """

# --- 5. EL ARRANQUE INVERTIDO (Solución al error de Asyncio) ---
def run_flask():
    # Esta función corre el servidor web en segundo plano
    app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__ ':
    # 1. Prendemos el servidor Web en un hilo paralelo (Background)
    #    Lo ponemos como 'daemon' para que se apague si el bot se apaga.
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🚀 Servidor Web iniciado en background...")
    
    # 2. Prendemos el Bot en el Hilo Principal (Main Thread)
    #    Esto satisface a Asyncio y evita el RuntimeError.
    print("🤖 Iniciando Bot de Telegram en Main Thread...")
    run_bot()