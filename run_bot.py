#!/usr/bin/env python3
"""
Script principal para ejecutar el Nexus Bot
"""

import os
import sys
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application

# Cargar variables de entorno
load_dotenv()

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def check_requirements():
    """Verifica que todos los requisitos estén instalados"""
    try:
        import telegram
        from telegram.ext import Application
        import requests
        logger.info("✅ Todas las dependencias están instaladas")
        return True
    except ImportError as e:
        logger.error(f"❌ Falta dependencia: {e}")
        logger.error("Ejecuta: pip install -r requirements_bot.txt")
        return False

def check_environment():
    """Verifica variables de entorno"""
    # Verificar variables de entorno críticas
    required_vars = ['BOT_TOKEN']
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        logger.error(f"❌ Variables de entorno faltantes: {missing_vars}")
        logger.error("💡 Configura las variables en Secrets:")
        logger.error("   - BOT_TOKEN: Token de tu bot de Telegram")
        return False

    # Verificar variables de MongoDB (opcionales pero recomendadas)
    mongodb_url = os.getenv('MONGODB_URL') or os.getenv('MONGODB_CONNECTION_STRING')
    if not mongodb_url:
        logger.warning("⚠️ MONGODB_URL no configurado")
        logger.info("💡 Para usar MongoDB, configura en Secrets:")
        logger.info("   - MONGODB_URL: Cadena de conexión de MongoDB Atlas")
        logger.info("   - MONGODB_DB_NAME: Nombre de la base de datos (opcional)")
    else:
        logger.info("✅ Variables de MongoDB encontradas")

    logger.info("✅ Variables de entorno configuradas")
    return True

def main():
    """Función principal"""
    logger.info("🚀 Iniciando Nexus Bot...")

    # Verificar requisitos
    if not check_requirements():
        sys.exit(1)

    if not check_environment():
        sys.exit(1)

    try:
        # Importar y ejecutar el bot
        from telegram_bot import main as run_bot
        run_bot()
    except KeyboardInterrupt:
        logger.info("🛑 Bot detenido por el usuario")
    except Exception as e:
        logger.error(f"❌ Error crítico: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
