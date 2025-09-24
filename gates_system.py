import asyncio
import random
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TimedOut

# Configurar logger específico para gates
logger = logging.getLogger(__name__)

# La instancia db se pasará al constructor
db = None

# Blacklist de tarjetas conocidas como inválidas o de test
BLACKLIST_CARDS = {
    '4242424242424242',
    '4000000000000002',
    '4000000000000127',
    '4000000000000119',
    '4000000000000341',
    '4000000000009995',
    '5555555555554444',
    '5200828282828210',
    '5105105105105100',
    '4111111111111111',
    '4012888888881881'
}

# BINs reales conocidos (no de test)
REAL_BINS = {
    # Visa
    '4532': 'Visa Credit',
    '4485': 'Visa Debit', 
    '4539': 'Visa Credit',
    '4916': 'Visa Debit',
    '4929': 'Visa Credit',
    # Mastercard
    '5431': 'Mastercard Credit',
    '5186': 'Mastercard Debit',
    '5204': 'Mastercard Credit',
    '5370': 'Mastercard Credit',
    # American Express
    '3782': 'American Express',
    '3714': 'American Express',
    # Discover
    '6011': 'Discover',
    '6445': 'Discover'
}

# BINs de test conocidos que deben ser rechazados
TEST_BINS = {'4242', '4000', '4001', '5555', '5200', '5105', '4111', '4012'}

def luhn_check(card_number: str) -> bool:
    """Verificar algoritmo de Luhn para validación de tarjetas"""
    def luhn_checksum(card_num):
        def digits_of(n):
            return [int(d) for d in str(n)]
        digits = digits_of(card_num)
        odd_digits = digits[-1::-2]
        even_digits = digits[-2::-2]
        checksum = sum(odd_digits)
        for d in even_digits:
            checksum += sum(digits_of(d*2))
        return checksum % 10
    
    return luhn_checksum(card_number) == 0

def validate_expiry_date(month: str, year: str) -> bool:
    """Validar fecha de vencimiento"""
    try:
        month_int = int(month)
        year_int = int(year)
        
        # Normalizar año a 4 dígitos
        if year_int < 100:
            year_int = 2000 + year_int
        
        # Verificar que mes esté en rango válido
        if month_int < 1 or month_int > 12:
            return False
        
        # Verificar que no esté expirada
        current_date = datetime.now()
        expiry_date = datetime(year_int, month_int, 1)
        
        # Si el año es el actual, verificar el mes
        if year_int == current_date.year:
            return month_int >= current_date.month
        
        # Si es año futuro, es válida
        return year_int > current_date.year
        
    except (ValueError, TypeError):
        return False

def validate_cvv(cvv: str, card_type: str = 'unknown') -> bool:
    """Validar CVV según el tipo de tarjeta"""
    if not cvv.isdigit():
        return False
    
    # American Express requiere 4 dígitos
    if card_type == 'amex' and len(cvv) == 4:
        return True
    # Otras tarjetas requieren 3 dígitos
    elif card_type != 'amex' and len(cvv) == 3:
        return True
    # Si no conocemos el tipo, aceptar 3 o 4 dígitos
    elif card_type == 'unknown' and len(cvv) in [3, 4]:
        return True
    
    return False

def get_card_type(card_number: str) -> str:
    """Determinar tipo de tarjeta basado en el BIN"""
    if card_number.startswith(('4',)):
        return 'visa'
    elif card_number.startswith(('51', '52', '53', '54', '55')):
        return 'mastercard'
    elif card_number.startswith(('34', '37')):
        return 'amex'
    elif card_number.startswith(('6011', '644', '645', '646', '647', '648', '649', '65')):
        return 'discover'
    else:
        return 'unknown'

def is_test_card(card_number: str) -> bool:
    """Verificar si es una tarjeta de test conocida"""
    # Verificar blacklist exacta
    if card_number in BLACKLIST_CARDS:
        return True
    
    # Verificar BINs de test con prefijos específicos más precisos
    test_bins_precise = {
        # Visa test cards
        '400000',   # Stripe test visa
        '424242',   # Stripe test visa  
        '411111',   # Test visa
        '401288',   # Test visa
        '400001',   # Visa test
        '400002',   # Visa test
        '400127',   # Visa test
        '400341',   # Visa test
        '400995',   # Visa test
        # Mastercard test cards
        '555555',   # Stripe test mastercard
        '520082',   # Stripe test mastercard
        '510510',   # Test mastercard
        '222100',   # Mastercard test
        '540005',   # Mastercard test
        # American Express test cards
        '378282',   # Amex test
        '371449',   # Amex test
        '378734',   # Amex test
        '371144',   # Amex test
        '378289',   # Amex test
        # Discover test cards
        '601111',   # Discover test
        '644564',   # Discover test
        '650002',   # Discover test
        # Other test cards
        '300000',   # Diners test
        '360000'    # Diners test
    }
    
    # Verificar BINs de test precisos (6 dígitos)
    for test_bin in test_bins_precise:
        if card_number.startswith(test_bin):
            return True
    
    return False

def pre_validation(card_number: str, exp_month: str, exp_year: str, cvv: str, gateway_name: str = "Unknown") -> dict:
    """
    Función centralizada de validación para TODOS los gates.
    Retorna dict con 'valid': bool y 'error_response': dict si no es válido
    """
    # 1. Verificar algoritmo de Luhn
    if not luhn_check(card_number):
        return {
            'valid': False,
            'error_response': {
                'success': False,
                'message': '❌ Card declined - Invalid card number (Luhn check failed)',
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False
            }
        }

    # 2. Verificar si es tarjeta de test
    if is_test_card(card_number):
        return {
            'valid': False,
            'error_response': {
                'success': False,
                'message': '❌ Card declined - Test card detected',
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False
            }
        }

    # 3. Verificar fecha de vencimiento
    if not validate_expiry_date(exp_month, exp_year):
        return {
            'valid': False,
            'error_response': {
                'success': False,
                'message': '❌ Card declined - Card expired or invalid expiry date',
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False
            }
        }

    # 4. Verificar CVV (CORREGIDA - no da bonus a todos los CVVs)
    card_type = get_card_type(card_number)
    if not validate_cvv(cvv, card_type):
        return {
            'valid': False,
            'error_response': {
                'success': False,
                'message': '❌ Card declined - Invalid CVV format',
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False
            }
        }

    # Si llega aquí, todas las validaciones pasaron
    return {'valid': True}

def calculate_realistic_success_rate(card_number: str, card_type: str, session_data: dict = None) -> float:
    """
    Calcular tasa de éxito ULTRA REALISTA centralizada para todos los gates.
    MÁXIMO 5% - Base extremadamente conservadora para mayor credibilidad.
    Implementa variabilidad por sesión y diferentes factores de riesgo.
    """
    # Base ultra conservadora - solo 0.5% inicial 
    success_rate = 0.005  # 0.5% base (1 de cada 200 tarjetas)

    # Verificar BINs reales conocidos (MUY selectivos)
    bin_6 = card_number[:6] if len(card_number) >= 6 else card_number[:4]
    
    # BINs premium muy específicos (solo los más confiables)
    premium_bank_bins = {
        '453259', '448590', '453962',  # Visa premium específicos
        '543159', '518671', '520455',  # Mastercard business específicos  
        '374245', '374553'             # Amex corporate específicos
    }
    
    # Bonificaciones mínimas y muy controladas
    if bin_6 in premium_bank_bins:
        success_rate += 0.015  # +1.5% solo para BINs premium verificados
    elif card_type in ['visa', 'mastercard']:
        success_rate += 0.008  # +0.8% para tipos comunes
    elif card_type == 'amex':
        success_rate += 0.005  # +0.5% para amex

    # Factor de sesión - si es el primer intento de la sesión, menor probabilidad
    if session_data:
        attempts_in_session = session_data.get('attempts', 0)
        if attempts_in_session == 0:
            success_rate *= 0.7  # Reducir 30% en primer intento
        elif attempts_in_session > 10:
            success_rate *= 0.3  # Reducir 70% si ya hay muchos intentos

    # Factor de aleatoriedad más agresivo para reducir patrones
    success_rate *= random.uniform(0.4, 0.9)  # Reducción más agresiva

    # MÁXIMO ULTRA REALISTA del 5% (1 de cada 20 en el mejor caso)
    success_rate = min(success_rate, 0.05)  # Máximo 5%
    success_rate = max(success_rate, 0.002)  # Mínimo 0.2% (1 de cada 500)

    return success_rate

class GateSystem:
    def __init__(self, database_instance):
        self.db = database_instance
        # Actualizar la referencia global para compatibilidad
        global db
        db = database_instance
        self.active_sessions = {}  # Sesiones activas de gates
        self.rate_limit_tracker = {}  # Control de rate limiting
        self.verification_history = {}  # Historial de verificaciones por usuario
        self.session_counters = {}  # Contadores por sesión de usuario

    def is_authorized(self, user_id: str) -> bool:
        """Verificar si el usuario tiene acceso usando la base de datos MongoDB"""
        try:
            # Verificar roles de staff usando MongoDB
            if self.db.is_founder(user_id):
                logger.info(f"[GATES] Usuario {user_id} autorizado como FUNDADOR")
                return True

            if self.db.is_cofounder(user_id):
                logger.info(f"[GATES] Usuario {user_id} autorizado como CO-FUNDADOR")
                return True

            if self.db.is_moderator(user_id):
                logger.info(f"[GATES] Usuario {user_id} autorizado como MODERADOR")
                return True

            # Obtener datos del usuario desde MongoDB
            user_data = self.db.get_user(user_id)
            is_premium = user_data.get('premium', False)
            premium_until = user_data.get('premium_until')

            logger.info(f"[GATES] VERIFICACIÓN - Usuario {user_id}: premium={is_premium}, until={premium_until}")

            # Si premium=False explícitamente, denegar inmediatamente
            if is_premium is False:
                logger.info(f"[GATES] Usuario {user_id} - Premium False - ACCESO DENEGADO ❌")
                return False

            # Lógica para premium=True
            if is_premium is True:
                if premium_until:
                    try:
                        # Parsear fecha de expiración
                        if isinstance(premium_until, str):
                            premium_until_date = datetime.fromisoformat(premium_until)
                        else:
                            premium_until_date = premium_until

                        # Verificar si aún es válido
                        if datetime.now() < premium_until_date:
                            logger.info(f"[GATES] Usuario {user_id} - Premium válido hasta {premium_until_date} ✅")
                            return True
                        else:
                            # Premium expirado - actualizar automáticamente
                            logger.info(f"[GATES] Usuario {user_id} - Premium expirado, actualizando BD")
                            self.db.update_user(user_id, {'premium': False, 'premium_until': None})
                            return False
                    except Exception as date_error:
                        logger.error(f"[GATES] Error fecha premium {user_id}: {date_error}")
                        # Si no hay fecha válida, es premium permanente
                        if premium_until is None:
                            logger.info(f"[GATES] Usuario {user_id} - Premium permanente (sin fecha) ✅")
                            return True
                        else:
                            logger.warning(f"[GATES] Usuario {user_id} - Error en fecha premium, DENEGANDO por seguridad ❌")
                            return False
                else:
                    # Premium=True sin fecha = premium permanente
                    logger.info(f"[GATES] Usuario {user_id} - Premium permanente (sin until) ✅")
                    return True

            # Usuario sin premium ni staff
            logger.info(f"[GATES] Usuario {user_id} - SIN ACCESO (premium={is_premium}, staff=False) ❌")
            return False

        except Exception as e:
            logger.error(f"[GATES] Error crítico verificando {user_id}: {e}")
            return False

    def check_rate_limit(self, user_id: str) -> dict:
        """
        Verificar rate limiting para evitar verificaciones masivas.
        Retorna dict con 'allowed': bool y 'wait_time': int si está limitado
        """
        current_time = datetime.now()
        user_tracker = self.rate_limit_tracker.get(user_id, {})
        
        # Límites por tiempo
        hourly_limit = 50  # Máximo 50 verificaciones por hora
        daily_limit = 200  # Máximo 200 verificaciones por día
        
        # Verificar límite por hora
        hourly_requests = user_tracker.get('hourly_requests', [])
        hourly_requests = [req_time for req_time in hourly_requests 
                          if current_time - req_time < timedelta(hours=1)]
        
        if len(hourly_requests) >= hourly_limit:
            wait_time = int((hourly_requests[0] + timedelta(hours=1) - current_time).total_seconds())
            logger.warning(f"[RATE_LIMIT] Usuario {user_id} excedió límite por hora ({len(hourly_requests)}/{hourly_limit})")
            return {
                'allowed': False,
                'wait_time': wait_time,
                'reason': f'Rate limit exceeded: {len(hourly_requests)}/{hourly_limit} requests in last hour'
            }
        
        # Verificar límite por día
        daily_requests = user_tracker.get('daily_requests', [])
        daily_requests = [req_time for req_time in daily_requests 
                         if current_time - req_time < timedelta(days=1)]
        
        if len(daily_requests) >= daily_limit:
            wait_time = int((daily_requests[0] + timedelta(days=1) - current_time).total_seconds())
            logger.warning(f"[RATE_LIMIT] Usuario {user_id} excedió límite diario ({len(daily_requests)}/{daily_limit})")
            return {
                'allowed': False,
                'wait_time': wait_time,
                'reason': f'Daily limit exceeded: {len(daily_requests)}/{daily_limit} requests in last 24h'
            }
        
        # Actualizar contadores
        hourly_requests.append(current_time)
        daily_requests.append(current_time)
        
        self.rate_limit_tracker[user_id] = {
            'hourly_requests': hourly_requests,
            'daily_requests': daily_requests,
            'last_request': current_time
        }
        
        logger.info(f"[RATE_LIMIT] Usuario {user_id} autorizado - {len(hourly_requests)}/{hourly_limit}h, {len(daily_requests)}/{daily_limit}d")
        return {'allowed': True}

    def create_or_get_session(self, user_id: str, gateway_name: str) -> str:
        """
        Obtener sesión existente o crear una nueva si no existe o expiró.
        Retorna session_id para reutilización.
        """
        session_ttl = timedelta(minutes=30)  # TTL de 30 minutos
        current_time = datetime.now()
        
        # Buscar sesión existente activa
        for session_id, session_data in self.active_sessions.items():
            if (session_data['user_id'] == user_id and 
                session_data['gateway'] == gateway_name and
                current_time - session_data['last_activity'] < session_ttl):
                
                logger.info(f"[SESSION] Reutilizando sesión existente: {session_id}")
                return session_id
        
        # Crear nueva sesión si no existe una activa
        return self.create_user_session(user_id, gateway_name)

    def create_user_session(self, user_id: str, gateway_name: str) -> str:
        """
        Crear sesión única para verificaciones de usuario.
        Retorna session_id único.
        """
        session_id = f"{user_id}_{gateway_name}_{int(datetime.now().timestamp())}"
        
        self.active_sessions[session_id] = {
            'user_id': user_id,
            'gateway': gateway_name,
            'start_time': datetime.now(),
            'attempts': 0,
            'live_count': 0,
            'dead_count': 0,
            'last_activity': datetime.now()
        }
        
        # Inicializar contador de usuario si no existe
        if user_id not in self.session_counters:
            self.session_counters[user_id] = {
                'total_sessions': 0,
                'total_attempts': 0,
                'total_live': 0,
                'first_activity': datetime.now()
            }
        
        self.session_counters[user_id]['total_sessions'] += 1
        
        logger.info(f"[SESSION] Nueva sesión creada: {session_id} para usuario {user_id}")
        return session_id

    def update_session_stats(self, session_id: str, result: dict):
        """
        Actualizar estadísticas de la sesión después de una verificación.
        """
        if session_id not in self.active_sessions:
            return
        
        session = self.active_sessions[session_id]
        user_id = session['user_id']
        
        # Actualizar sesión
        session['attempts'] += 1
        session['last_activity'] = datetime.now()
        
        if result.get('is_live', False):
            session['live_count'] += 1
            self.session_counters[user_id]['total_live'] += 1
        else:
            session['dead_count'] += 1
        
        # Actualizar contador global del usuario
        self.session_counters[user_id]['total_attempts'] += 1
        
        # Log detallado para monitorización
        logger.info(f"[SESSION_STATS] {session_id} - Attempts: {session['attempts']}, "
                   f"Live: {session['live_count']}, Dead: {session['dead_count']}, "
                   f"Success Rate: {(session['live_count']/session['attempts']*100):.1f}%")

    def get_session_data(self, session_id: str) -> dict:
        """
        Obtener datos de la sesión para cálculos de probabilidad.
        """
        if session_id not in self.active_sessions:
            return {'attempts': 0, 'live_count': 0}
        
        return self.active_sessions[session_id]

    def log_verification_attempt(self, user_id: str, card_data: str, gateway: str, result: dict):
        """
        Registrar intento de verificación para monitorización y análisis.
        """
        timestamp = datetime.now()
        
        # Inicializar historial del usuario si no existe
        if user_id not in self.verification_history:
            self.verification_history[user_id] = []
        
        # Enmascarar número de tarjeta para seguridad
        masked_card = card_data.split('|')[0]
        masked_card = f"{masked_card[:6]}****{masked_card[-4:]}" if len(masked_card) >= 10 else "****"
        
        log_entry = {
            'timestamp': timestamp.isoformat(),
            'user_id': user_id,
            'gateway': gateway,
            'card_masked': masked_card,
            'result': result.get('status', 'UNKNOWN'),
            'is_live': result.get('is_live', False),
            'error_code': result.get('error_code'),
            'error_reason': result.get('error_reason'),
            'processing_time': result.get('processing_time'),
            'success_rate_used': result.get('success_rate_applied', 0)
        }
        
        # Mantener solo últimos 100 registros por usuario
        self.verification_history[user_id].append(log_entry)
        if len(self.verification_history[user_id]) > 100:
            self.verification_history[user_id] = self.verification_history[user_id][-100:]
        
        # Log estructurado para análisis
        logger.info(f"[VERIFICATION_LOG] User: {user_id}, Gateway: {gateway}, "
                   f"Card: {masked_card}, Result: {result.get('status')}, "
                   f"Live: {result.get('is_live')}, Code: {result.get('error_code', 'N/A')}")

    def cleanup_old_sessions(self):
        """
        Limpiar sesiones antigas para evitar acumulación de memoria.
        """
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(hours=2)  # Limpiar sesiones de más de 2 horas
        
        old_sessions = [session_id for session_id, session_data in self.active_sessions.items()
                       if session_data['last_activity'] < cutoff_time]
        
        for session_id in old_sessions:
            del self.active_sessions[session_id]
            logger.info(f"[CLEANUP] Sesión limpiada: {session_id}")
        
        if old_sessions:
            logger.info(f"[CLEANUP] {len(old_sessions)} sesiones antiguas eliminadas")

    def create_gates_menu(self) -> InlineKeyboardMarkup:
        """Crear menú principal de gates"""
        keyboard = [
            [
                InlineKeyboardButton("🔵 Stripe Gate", callback_data='gate_stripe'),
                InlineKeyboardButton("🟠 Amazon Gate", callback_data='gate_amazon')
            ],
            [
                InlineKeyboardButton("🔴 PayPal Gate", callback_data='gate_paypal'),
                InlineKeyboardButton("🟡 Ayden Gate", callback_data='gate_ayden')
            ],
            [
                InlineKeyboardButton("🟢 Auth Gate", callback_data='gate_auth'),
                InlineKeyboardButton("⚫ CCN Charge", callback_data='gate_ccn')
            ],
            [
                InlineKeyboardButton("🤖 CyberSource AI", callback_data='gate_cybersource'),
                InlineKeyboardButton("🇬🇧 Worldpay UK", callback_data='gate_worldpay')
            ],
            [
                InlineKeyboardButton("🌐 Braintree Pro", callback_data='gate_braintree'),
                InlineKeyboardButton("📊 Gate Status", callback_data='gates_status')
            ],
            [
                InlineKeyboardButton("❌ Cerrar", callback_data='gates_close')
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def process_stripe_gate(self, card_data: str, user_id: str = None) -> dict:
        """Procesar verificación Stripe Gate - SISTEMA ULTRA REALISTA INTEGRADO"""
        await asyncio.sleep(random.uniform(2.0, 4.0))
        
        gateway_name = 'Stripe Ultra'
        
        # 1. Verificar autorización del usuario
        if user_id and not self.is_authorized(user_id):
            result = {
                'success': False,
                'message': '❌ Access denied - Premium membership required',
                'status': 'DENIED',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': 'AUTH_REQUIRED',
                'error_reason': 'authorization_failed'
            }
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result
        
        # 2. Verificar rate limiting
        if user_id:
            rate_check = self.check_rate_limit(user_id)
            if not rate_check['allowed']:
                result = {
                    'success': False,
                    'message': f"❌ Rate limit exceeded - Wait {rate_check['wait_time']} seconds",
                    'status': 'RATE_LIMITED',
                    'gateway': gateway_name,
                    'amount': '$0.00',
                    'is_live': False,
                    'error_code': 'RATE_LIMIT',
                    'error_reason': 'too_many_requests',
                    'wait_time': rate_check['wait_time']
                }
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
                return result

        # 3. Validar formato de entrada
        parts = card_data.split('|')
        if len(parts) < 4:
            result = {
                'success': False,
                'message': '❌ Formato inválido - Use: 4532123456781234|12|25|123',
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': 'INVALID_FORMAT',
                'error_reason': 'invalid_input'
            }
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # 4. Usar validación centralizada
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, gateway_name)
        if not validation_result['valid']:
            result = validation_result['error_response']
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result

        # 5. Crear/obtener sesión de usuario (reutilizar existente si es posible)
        session_id = None
        if user_id:
            session_id = self.create_or_get_session(user_id, gateway_name)
            session_data = self.get_session_data(session_id)
        else:
            session_data = {'attempts': 0}

        # 6. Usar algoritmo ultra realista con datos reales de sesión
        card_type = get_card_type(card_number)
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)
        
        is_success = random.random() < success_rate
        
        logger.info(f"[STRIPE] Tarjeta {card_number[:6]}****{card_number[-4:]} - Rate: {success_rate:.3f} - Result: {'LIVE' if is_success else 'DEAD'}")

        if is_success:
            # Respuestas de éxito más específicas y variadas
            success_responses = [
                "✅ Payment successful - $1.00 charged and captured - TXN: " + str(random.randint(100000, 999999)),
                "✅ Transaction approved - CVV2/AVS Match - Risk score: Low (14) - Funds reserved",
                "✅ Stripe: Payment processed - Gateway Response: 00 - Merchant cleared",
                "✅ Card verified $1.00 - Issuer auth: APPROVED - Settlement pending",
                "✅ Authorization successful - Fraud check passed - Card validated for commerce",
                "✅ Payment captured - Network: Visa/MC - Processing time: 1.2s",
                "✅ Transaction complete - 3DS not required - Funds available"
            ]
            result = {
                'success': True,
                'message': random.choice(success_responses),
                'status': 'LIVE',
                'gateway': gateway_name,
                'amount': '$1.00',
                'is_live': True,
                'response_code': '00',
                'risk_score': random.randint(5, 25),
                'processing_time': f"{random.uniform(0.8, 2.1):.1f}s",
                'success_rate_applied': success_rate
            }
        else:
            # Respuestas de error más específicas y realistas por tipo
            error_types = [
                # Errores de fondos
                {
                    'message': "❌ Card declined - Insufficient funds (NSF)",
                    'code': '05',
                    'reason': 'insufficient_funds'
                },
                {
                    'message': "❌ Transaction failed - Insufficient credit limit",
                    'code': '61',
                    'reason': 'credit_limit_exceeded'
                },
                # Errores de seguridad
                {
                    'message': "❌ Payment declined - Fraud protection triggered",
                    'code': '59',
                    'reason': 'fraud_detected'
                },
                {
                    'message': "❌ Card blocked - Security verification required",
                    'code': '14',
                    'reason': 'security_violation'
                },
                {
                    'message': "❌ Risk threshold exceeded - Velocity check failed",
                    'code': '61',
                    'reason': 'velocity_limit'
                },
                # Errores del emisor
                {
                    'message': "❌ Generic decline - Contact issuing bank",
                    'code': '05',
                    'reason': 'generic_decline'
                },
                {
                    'message': "❌ Issuer unavailable - Authorization timeout",
                    'code': '91',
                    'reason': 'issuer_timeout'
                },
                # Errores técnicos
                {
                    'message': "❌ Gateway error - Network communication failed",
                    'code': '96',
                    'reason': 'network_error'
                },
                {
                    'message': "❌ Processing error - Invalid merchant configuration",
                    'code': '03',
                    'reason': 'invalid_merchant'
                }
            ]
            
            selected_error = random.choice(error_types)
            result = {
                'success': False,
                'message': selected_error['message'],
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': selected_error['code'],
                'error_reason': selected_error['reason'],
                'processing_time': f"{random.uniform(0.3, 1.5):.1f}s",
                'success_rate_applied': success_rate
            }
        
        # 7. Actualizar estadísticas de sesión y logging
        if user_id and session_id:
            self.update_session_stats(session_id, result)
            self.log_verification_attempt(user_id, card_data, gateway_name, result)
            
            # Limpiar sesiones antigas periódicamente
            if random.random() < 0.1:  # 10% de probabilidad
                self.cleanup_old_sessions()
        
        return result

    async def process_amazon_gate(self, card_data: str, user_id: str = None) -> dict:
        """Procesar verificación Amazon Gate - SISTEMA ULTRA REALISTA INTEGRADO"""
        await asyncio.sleep(random.uniform(3.0, 5.0))
        
        gateway_name = 'Amazon Prime'
        
        # 1. Verificar autorización del usuario
        if user_id and not self.is_authorized(user_id):
            result = {
                'success': False,
                'message': '❌ Access denied - Premium membership required',
                'status': 'DENIED',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': 'AUTH_REQUIRED',
                'error_reason': 'authorization_failed'
            }
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result
        
        # 2. Verificar rate limiting
        if user_id:
            rate_check = self.check_rate_limit(user_id)
            if not rate_check['allowed']:
                result = {
                    'success': False,
                    'message': f"❌ Rate limit exceeded - Wait {rate_check['wait_time']} seconds",
                    'status': 'RATE_LIMITED',
                    'gateway': gateway_name,
                    'amount': '$0.00',
                    'is_live': False,
                    'error_code': 'RATE_LIMIT',
                    'error_reason': 'too_many_requests',
                    'wait_time': rate_check['wait_time']
                }
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
                return result

        # 3. Validar formato de entrada
        parts = card_data.split('|')
        if len(parts) < 4:
            result = {
                'success': False,
                'message': '❌ Formato inválido - Use: 4532123456781234|12|25|123',
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': 'INVALID_FORMAT',
                'error_reason': 'invalid_input'
            }
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # 4. Usar validación centralizada
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, gateway_name)
        if not validation_result['valid']:
            result = validation_result['error_response']
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result

        # 5. Crear/obtener sesión de usuario (reutilizar existente si es posible)
        session_id = None
        if user_id:
            session_id = self.create_or_get_session(user_id, gateway_name)
            session_data = self.get_session_data(session_id)
        else:
            session_data = {'attempts': 0}

        # 6. Usar algoritmo ultra realista con datos reales de sesión
        card_type = get_card_type(card_number)
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)
        
        is_success = random.random() < success_rate
        
        logger.info(f"[AMAZON] Tarjeta {card_number[:6]}****{card_number[-4:]} - Rate: {success_rate:.3f} - Result: {'LIVE' if is_success else 'DEAD'}")

        if is_success:
            # Respuestas de éxito específicas para Amazon
            success_responses = [
                "✅ Amazon: Payment method verified - $1.00 charged successfully",
                "✅ Amazon: Card added to account - $1.00 authorization complete",
                "✅ Amazon: Billing updated - $1.00 processed for validation",
                "✅ Amazon: Payment gateway approved - Ready for Prime purchases"
            ]
            result = {
                'success': True,
                'message': random.choice(success_responses),
                'status': 'LIVE',
                'gateway': gateway_name,
                'amount': '$1.00',
                'is_live': True,
                'response_code': '00',
                'risk_score': random.randint(8, 30),
                'processing_time': f"{random.uniform(1.2, 3.0):.1f}s",
                'success_rate_applied': success_rate
            }
        else:
            # Respuestas de error específicas para Amazon
            error_types = [
                {
                    'message': "❌ Amazon: Payment method declined by issuer",
                    'code': 'PM_DECLINED',
                    'reason': 'payment_declined'
                },
                {
                    'message': "❌ Amazon: Card verification failed - CVV mismatch",
                    'code': 'CVV_FAIL',
                    'reason': 'cvv_verification_failed'
                },
                {
                    'message': "❌ Amazon: Unable to add card - Billing address required",
                    'code': 'BILLING_ERROR',
                    'reason': 'billing_address_mismatch'
                },
                {
                    'message': "❌ Amazon: Security review required - High risk transaction",
                    'code': 'SECURITY_REVIEW',
                    'reason': 'fraud_protection'
                }
            ]
            
            selected_error = random.choice(error_types)
            result = {
                'success': False,
                'message': selected_error['message'],
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': selected_error['code'],
                'error_reason': selected_error['reason'],
                'processing_time': f"{random.uniform(0.8, 2.5):.1f}s",
                'success_rate_applied': success_rate
            }
        
        # 7. Actualizar estadísticas de sesión y logging
        if user_id and session_id:
            self.update_session_stats(session_id, result)
            self.log_verification_attempt(user_id, card_data, gateway_name, result)
            
            # Limpiar sesiones antigas periódicamente
            if random.random() < 0.1:  # 10% de probabilidad
                self.cleanup_old_sessions()
        
        return result

    async def process_paypal_gate(self, card_data: str, user_id: str = None) -> dict:
        """Procesar verificación PayPal Gate - SISTEMA ULTRA REALISTA INTEGRADO"""
        await asyncio.sleep(random.uniform(2.5, 4.5))
        
        gateway_name = 'PayPal Express'
        
        # 1. Verificar autorización del usuario
        if user_id and not self.is_authorized(user_id):
            result = {
                'success': False,
                'message': '❌ Access denied - Premium membership required',
                'status': 'DENIED',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': 'AUTH_REQUIRED',
                'error_reason': 'authorization_failed'
            }
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result
        
        # 2. Verificar rate limiting
        if user_id:
            rate_check = self.check_rate_limit(user_id)
            if not rate_check['allowed']:
                result = {
                    'success': False,
                    'message': f"❌ Rate limit exceeded - Wait {rate_check['wait_time']} seconds",
                    'status': 'RATE_LIMITED',
                    'gateway': gateway_name,
                    'amount': '$0.00',
                    'is_live': False,
                    'error_code': 'RATE_LIMIT',
                    'error_reason': 'too_many_requests',
                    'wait_time': rate_check['wait_time']
                }
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
                return result

        # 3. Validar formato de entrada
        parts = card_data.split('|')
        if len(parts) < 4:
            result = {
                'success': False,
                'message': '❌ Formato inválido - Use: 4532123456781234|12|25|123',
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': 'INVALID_FORMAT',
                'error_reason': 'invalid_input'
            }
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # 4. Usar validación centralizada
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, gateway_name)
        if not validation_result['valid']:
            result = validation_result['error_response']
            if user_id:
                self.log_verification_attempt(user_id, card_data, gateway_name, result)
            return result

        # 5. Crear/obtener sesión de usuario (reutilizar existente si es posible)
        session_id = None
        if user_id:
            session_id = self.create_or_get_session(user_id, gateway_name)
            session_data = self.get_session_data(session_id)
        else:
            session_data = {'attempts': 0}

        # 6. Usar algoritmo ultra realista con datos reales de sesión
        card_type = get_card_type(card_number)
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)
        
        is_success = random.random() < success_rate
        
        logger.info(f"[PAYPAL] Tarjeta {card_number[:6]}****{card_number[-4:]} - Rate: {success_rate:.3f} - Result: {'LIVE' if is_success else 'DEAD'}")

        if is_success:
            # Respuestas de éxito específicas para PayPal
            success_responses = [
                "✅ PayPal: Card linked successfully - $1.00 charged for verification",
                "✅ PayPal: Payment method added - $1.00 authorization complete",
                "✅ PayPal: Account verified - $1.00 processed successfully",
                "✅ PayPal: Transaction approved - Ready for Express Checkout"
            ]
            result = {
                'success': True,
                'message': random.choice(success_responses),
                'status': 'LIVE',
                'gateway': gateway_name,
                'amount': '$1.00',
                'is_live': True,
                'response_code': '00',
                'risk_score': random.randint(5, 25),
                'processing_time': f"{random.uniform(0.9, 2.8):.1f}s",
                'success_rate_applied': success_rate
            }
        else:
            # Respuestas de error específicas para PayPal
            error_types = [
                {
                    'message': "❌ PayPal: Card verification failed - Invalid account details",
                    'code': 'CARD_VERIFICATION_FAILED',
                    'reason': 'verification_failed'
                },
                {
                    'message': "❌ PayPal: Unable to link card - Risk assessment failed",
                    'code': 'RISK_FAILED',
                    'reason': 'risk_assessment_failed'
                },
                {
                    'message': "❌ PayPal: Security check failed - Additional verification required",
                    'code': 'SECURITY_CHECK',
                    'reason': 'security_review_required'
                },
                {
                    'message': "❌ PayPal: Payment processor declined - Contact issuing bank",
                    'code': 'PROCESSOR_DECLINED',
                    'reason': 'processor_decline'
                }
            ]
            
            selected_error = random.choice(error_types)
            result = {
                'success': False,
                'message': selected_error['message'],
                'status': 'DEAD',
                'gateway': gateway_name,
                'amount': '$0.00',
                'is_live': False,
                'error_code': selected_error['code'],
                'error_reason': selected_error['reason'],
                'processing_time': f"{random.uniform(0.6, 2.2):.1f}s",
                'success_rate_applied': success_rate
            }
        
        # 7. Actualizar estadísticas de sesión y logging
        if user_id and session_id:
            self.update_session_stats(session_id, result)
            self.log_verification_attempt(user_id, card_data, gateway_name, result)
            
            # Limpiar sesiones antigas periódicamente
            if random.random() < 0.1:  # 10% de probabilidad
                self.cleanup_old_sessions()
        
        return result

    async def process_ayden_gate(self, card_data: str) -> dict:
        """Procesar verificación Ayden Gate - EFECTIVIDAD COMERCIAL"""
        await asyncio.sleep(random.uniform(3.5, 5.5))

        parts = card_data.split('|')
        if len(parts) < 4:
            return {
                'success': False,
                'message': '❌ Formato inválido',
                'status': 'DEAD'
            }

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # USAR VALIDACIÓN CENTRALIZADA (Luhn, test cards, fechas, CVV)
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, 'Ayden EU')
        if not validation_result['valid']:
            return validation_result['error_response']

        # USAR ALGORITMO ULTRA REALISTA CENTRALIZADO 
        card_type = get_card_type(card_number)
        session_data = {'attempts': 0}  # Datos básicos por defecto
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)

        is_success = random.random() < success_rate

        if is_success:
            responses = [
                "✅ Ayden: $1.00 payment authorized successfully",
                "✅ Ayden: Card charged $1.00 - Verification passed",
                "✅ Ayden: $1.00 transaction approved - EU gateway",
                "✅ Ayden: Payment processed $1.00 - 3DS bypass successful"
            ]
            return {
                'success': True,
                'message': random.choice(responses),
                'status': 'LIVE',
                'gateway': 'Ayden EU',
                'amount': '$1.00',
                'is_live': True
            }
        else:
            responses = [
                "❌ Ayden: Authorization declined",
                "❌ Ayden: Card not supported",
                "❌ Ayden: Risk score too high",
                "❌ Ayden: 3DS authentication failed"
            ]
            return {
                'success': False,
                'message': random.choice(responses),
                'status': 'DEAD',
                'gateway': 'Ayden EU',
                'amount': '$0.00',
                'is_live': False
            }

    async def process_auth_gate(self, card_data: str) -> dict:
        """Procesar verificación Auth Gate - EFECTIVIDAD REALISTA"""
        await asyncio.sleep(random.uniform(1.5, 3.0))

        parts = card_data.split('|')
        if len(parts) < 4:
            return {
                'success': False,
                'message': '❌ Formato inválido',
                'status': 'DEAD'
            }

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # USAR VALIDACIÓN CENTRALIZADA (Luhn, test cards, fechas, CVV)
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, 'Auth Check')
        if not validation_result['valid']:
            return validation_result['error_response']

        # USAR ALGORITMO ULTRA REALISTA CENTRALIZADO 
        card_type = get_card_type(card_number)
        session_data = {'attempts': 0}  # Datos básicos por defecto
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)

        is_success = random.random() < success_rate

        if is_success:
            return {
                'success': True,
                'message': "✅ Auth: Verification successful",
                'status': 'LIVE',
                'gateway': 'Auth Check',
                'amount': '$0.01',
                'is_live': True
            }
        else:
            responses = [
                "❌ Auth: Verification failed",
                "❌ Auth: Invalid card data",
                "❌ Auth: CVV check failed"
            ]
            return {
                'success': False,
                'message': random.choice(responses),
                'status': 'DEAD',
                'gateway': 'Auth Check',
                'amount': '$0.00',
                'is_live': False
            }

    async def process_ccn_charge(self, card_data: str) -> dict:
        """Procesar CCN Charge Gate - EFECTIVIDAD REALISTA"""
        await asyncio.sleep(random.uniform(2.0, 4.0))

        parts = card_data.split('|')
        if len(parts) < 4:
            return {
                'success': False,
                'message': '❌ Formato inválido',
                'status': 'DEAD'
            }

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # USAR VALIDACIÓN CENTRALIZADA (Luhn, test cards, fechas, CVV)
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, 'CCN Charge')
        if not validation_result['valid']:
            return validation_result['error_response']

        # USAR ALGORITMO ULTRA REALISTA CENTRALIZADO 
        card_type = get_card_type(card_number)
        session_data = {'attempts': 0}  # Datos básicos por defecto
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)

        is_success = random.random() < success_rate

        if is_success:
            responses = [
                "✅ CCN: Charge successful - $1.00 processed",
                "✅ CCN: Payment $1.00 processed - CVV verified",
                "✅ CCN: Transaction approved $1.00 - Low risk",
                "✅ CCN: $1.00 charged successfully - Funds captured"
            ]
            return {
                'success': True,
                'message': random.choice(responses),
                'status': 'LIVE',
                'gateway': 'CCN Charge',
                'amount': '$1.00',
                'is_live': True
            }
        else:
            responses = [
                "❌ CCN: Charge declined - Insufficient funds",
                "❌ CCN: Payment failed - Invalid card",
                "❌ CCN: Transaction denied - Bank decline",
                "❌ Risk threshold exceeded"
            ]
            return {
                'success': False,
                'message': random.choice(responses),
                'status': 'DEAD',
                'gateway': 'CCN Charge',
                'amount': '$0.00',
                'is_live': False
            }

    async def process_cybersource_ai(self, card_data: str) -> dict:
        """Procesar CyberSource AI Gate - INTELIGENCIA ARTIFICIAL ANTI-FRAUDE"""
        await asyncio.sleep(random.uniform(3.5, 6.0))  # IA toma más tiempo

        parts = card_data.split('|')
        if len(parts) < 4:
            return {
                'success': False,
                'message': '❌ Formato inválido',
                'status': 'DEAD'
            }

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # USAR VALIDACIÓN CENTRALIZADA (Luhn, test cards, fechas, CVV)
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, 'CyberSource AI')
        if not validation_result['valid']:
            return validation_result['error_response']

        # USAR ALGORITMO ULTRA REALISTA CENTRALIZADO 
        card_type = get_card_type(card_number)
        session_data = {'attempts': 0}  # Datos básicos por defecto
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)

        is_success = random.random() < success_rate

        if is_success:
            responses = [
                "✅ CyberSource AI: ACCEPT - Low risk score",
                "✅ CyberSource AI: AUTHORIZED - Pattern verified",
                "✅ CyberSource AI: SUCCESS - ML model approved",
                "✅ CyberSource AI: APPROVED - Fraud score: 0.12"
            ]
            return {
                'success': True,
                'message': random.choice(responses),
                'status': 'LIVE',
                'gateway': 'CyberSource AI',
                'amount': '$0.01',
                'is_live': True
            }
        else:
            responses = [
                "❌ CyberSource AI: REJECT - High risk score",
                "❌ CyberSource AI: DECLINED - ML flagged",
                "❌ CyberSource AI: BLOCKED - Fraud detection",
                "❌ CyberSource AI: REVIEW - Manual verification required",
                "❌ CyberSource AI: DENIED - Pattern anomaly detected"
            ]
            return {
                'success': False,
                'message': random.choice(responses),
                'status': 'DEAD',
                'gateway': 'CyberSource AI',
                'amount': '$0.00',
                'is_live': False
            }

    async def process_worldpay_gate(self, card_data: str) -> dict:
        """Procesar Worldpay Gate - PROCESAMIENTO BRITÁNICO PREMIUM"""
        await asyncio.sleep(random.uniform(2.5, 4.5))

        parts = card_data.split('|')
        if len(parts) < 4:
            return {
                'success': False,
                'message': '❌ Formato inválido',
                'status': 'DEAD'
            }

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # USAR VALIDACIÓN CENTRALIZADA (Luhn, test cards, fechas, CVV)
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, 'Worldpay UK')
        if not validation_result['valid']:
            return validation_result['error_response']

        # USAR ALGORITMO ULTRA REALISTA CENTRALIZADO 
        card_type = get_card_type(card_number)
        session_data = {'attempts': 0}  # Datos básicos por defecto
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)

        is_success = random.random() < success_rate

        if is_success:
            responses = [
                "✅ Worldpay: AUTHORISED - Payment captured",
                "✅ Worldpay: SUCCESS - Transaction settled",
                "✅ Worldpay: APPROVED - UK gateway response",
                "✅ Worldpay: CAPTURED - Funds secured"
            ]
            return {
                'success': True,
                'message': random.choice(responses),
                'status': 'LIVE',
                'gateway': 'Worldpay UK',
                'amount': '$0.30',
                'is_live': True
            }
        else:
            responses = [
                "❌ Worldpay: REFUSED - Issuer declined",
                "❌ Worldpay: FAILED - Card verification failed",
                "❌ Worldpay: CANCELLED - Risk assessment",
                "❌ Worldpay: BLOCKED - Fraud prevention",
                "❌ Worldpay: EXPIRED - Card invalid"
            ]
            return {
                'success': False,
                'message': random.choice(responses),
                'status': 'DEAD',
                'gateway': 'Worldpay UK',
                'amount': '$0.00',
                'is_live': False
            }

    async def process_braintree_gate(self, card_data: str) -> dict:
        """Procesar Braintree Gate - ANÁLISIS TEMPORAL AVANZADO"""
        await asyncio.sleep(random.uniform(2.0, 3.5))

        parts = card_data.split('|')
        if len(parts) < 4:
            return {
                'success': False,
                'message': '❌ Formato inválido',
                'status': 'DEAD'
            }

        card_number = parts[0].strip()
        exp_month = parts[1].strip()
        exp_year = parts[2].strip()
        cvv = parts[3].strip()

        # USAR VALIDACIÓN CENTRALIZADA (Luhn, test cards, fechas, CVV)
        validation_result = pre_validation(card_number, exp_month, exp_year, cvv, 'Braintree Pro')
        if not validation_result['valid']:
            return validation_result['error_response']

        # USAR ALGORITMO ULTRA REALISTA CENTRALIZADO 
        card_type = get_card_type(card_number)
        session_data = {'attempts': 0}  # Datos básicos por defecto
        success_rate = calculate_realistic_success_rate(card_number, card_type, session_data)

        is_success = random.random() < success_rate

        if is_success:
            responses = [
                "✅ Braintree: AUTHORIZED - Transaction approved",
                "✅ Braintree: SUCCESS - Payment processed",
                "✅ Braintree: APPROVED - Gateway response OK",
                "✅ Braintree: CAPTURED - Settlement pending"
            ]
            return {
                'success': True,
                'message': random.choice(responses),
                'status': 'LIVE',
                'gateway': 'Braintree Pro',
                'amount': '$0.25',
                'is_live': True
            }
        else:
            responses = [
                "❌ Braintree: DECLINED - Issuer refused",
                "❌ Braintree: FAILED - Card verification failed",
                "❌ Braintree: TIMEOUT - Gateway unavailable",
                "❌ Braintree: REJECTED - Risk assessment",
                "❌ Braintree: BLOCKED - Fraud protection"
            ]
            return {
                'success': False,
                'message': random.choice(responses),
                'status': 'DEAD',
                'gateway': 'Braintree Pro',
                'amount': '$0.00',
                'is_live': False
            }

    async def safe_edit_message(self, message, text, reply_markup=None, parse_mode=ParseMode.MARKDOWN):
        """Editar mensaje de forma segura con control de rate limiting"""
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except RetryAfter as e:
            # Esperar el tiempo requerido por Telegram
            await asyncio.sleep(e.retry_after + 1)
            try:
                await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                # Si falla de nuevo, enviar nuevo mensaje
                await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TimedOut:
            await asyncio.sleep(2)
            try:
                await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            # Como último recurso, enviar nuevo mensaje
            await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

# Instancia global del sistema de gates
gate_system = None

async def gates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando principal /gates - Todos pueden ver, solo premium/fundadores pueden usar"""
    global gate_system
    # Importar db aquí para asegurar que tenemos la instancia actual
    from telegram_bot import db as current_db
    if gate_system is None:
        gate_system = GateSystem(current_db)
    else:
        # Actualizar la referencia de la base de datos
        gate_system.db = current_db

    user_id = str(update.effective_user.id)

    # Verificar créditos (5 créditos por uso) - Solo si no es autorizado
    user_data = db.get_user(user_id)
    is_authorized = gate_system.is_authorized(user_id)

    # Los usuarios autorizados (premium/staff) no necesitan créditos
    if not is_authorized and user_data['credits'] < 5:
        await update.message.reply_text(
            "❌ **LOOT INSUFICIENTE** ❌\n\n"
            f"💰 **Necesitas:** 5 loot\n"
            f"💳 **Tienes:** {user_data['credits']} loot\n\n"
            "🎁 **Obtener más loot:**\n"
            "• `/loot` - Bono diario gratis\n"
            "• `/simulator` - Casino bot\n"
            "• Contactar administración",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Crear menú de gates
    keyboard = gate_system.create_gates_menu()

    # Determinar tipo de usuario y acceso
    is_founder = db.is_founder(user_id)
    is_cofounder = db.is_cofounder(user_id)
    is_moderator = db.is_moderator(user_id)
    is_authorized = gate_system.is_authorized(user_id)

    # Verificar premium - MEJORADO PARA DEPURACIÓN
    user_data = db.get_user(user_id)
    is_premium = user_data.get('premium', False)
    premium_until = user_data.get('premium_until')

    # Log detallado para depuración
    logger.info(f"Gates command - Usuario {user_id}: premium={is_premium}, until={premium_until}")

    # Verificar si el premium es válido
    premium_valid = False
    if is_premium:
        if premium_until:
            try:
                if isinstance(premium_until, str):
                    premium_until_date = datetime.fromisoformat(premium_until)
                else:
                    premium_until_date = premium_until

                if datetime.now() < premium_until_date:
                    premium_valid = True
                    logger.info(f"Premium válido hasta {premium_until_date}")
                else:
                    logger.info(f"Premium expirado en {premium_until_date}")
            except Exception as e:
                logger.error(f"Error verificando fecha premium: {e}")
                # Si hay error pero tiene premium=True, considerar válido
                premium_valid = True
        else:
            # Premium sin fecha = permanente
            premium_valid = True
            logger.info(f"Premium permanente detectado")

    # Determinar tipo de usuario y acceso basado en roles de staff y premium
    if is_founder:
        user_type = "👑 FUNDADOR"
        access_text = "✅ ACCESO COMPLETO"
        status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
        modules_status = "🔓"
        final_message = "➤ Selecciona tu módulo preferido:"
    elif is_cofounder:
        user_type = "💎 CO-FUNDADOR"
        access_text = "✅ ACCESO COMPLETO"
        status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
        modules_status = "🔓"
        final_message = "➤ Selecciona tu módulo preferido:"
    elif is_moderator:
        user_type = "🛡️ MODERADOR"
        access_text = "✅ ACCESO COMPLETO"
        status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
        modules_status = "🔓"
        final_message = "➤ Selecciona tu módulo preferido:"
    elif premium_valid:
        user_type = "💎 PREMIUM"
        access_text = "✅ ACCESO COMPLETO"
        status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
        modules_status = "🔓"
        final_message = "➤ Selecciona tu módulo preferido:"
    else:
        user_type = "🆓 USUARIO ESTÁNDAR"
        access_text = "❌ SOLO VISTA PREVIA"
        status_section = "[!] ACCESO A FUNCIONES DENEGADO\n[!] VISUALIZACIÓN TEMPORAL ACTIVADA"
        modules_status = "🔒"
        final_message = "➤ Desbloquea acceso total:\n    ↳ PREMIUM ACTIVATION: @Laleyendas01"

    # Plantilla unificada para todos los usuarios
    response = f"┏━━━━━━━━━━━━━━━┓\n"
    response += f"┃    GATES CORE   -  DARK ACCESS     ┃\n"
    response += f"┗━━━━━━━━━━━━━━━┛\n\n"
    response += f"✘ USUARIO: {user_type}\n"
    response += f"✘ ESTADO : {access_text}\n"
    response += f"✘ LOOT DISPONIBLE: {user_data['credits']}\n"
    response += f"✘ COSTO POR GATE: 1 🔻\n"
    response += f"✘ MÓDULOS RESTRINGIDOS: {modules_status}\n\n"
    response += f"──────────────────────────────\n"
    response += f"{status_section}\n"
    response += f"──────────────────────────────\n\n"
    response += f">> GATES DISPONIBLES:\n"
    response += f"│  → 🔹 Stripe                    → 🟠 Amazon\n"
    response += f"│  → 🔴 PayPal                   → 🟡 Ayden\n"
    response += f"│  → 🟢 Auth                       → ⚫ CCN Charge\n"
    response += f"│  → 🤖 CyberSource AI\n"
    response += f"│  → 🌐 Braintree Pro       → 🇬🇧 Worldpay UK\n\n"
    response += f"{final_message}"

    await update.message.reply_text(
        response,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_gate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejar callbacks de gates"""
    global gate_system
    query = update.callback_query
    user_id = str(query.from_user.id)

    # Importar db aquí para asegurar que tenemos la instancia actual
    from telegram_bot import db as current_db
    if gate_system is not None:
        gate_system.db = current_db

    await query.answer()

    if query.data == 'gates_close':
        await query.edit_message_text(
            "❌ **Gates System cerrado**\n\n"
            "💡 Usa `/gates` para acceder nuevamente",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if query.data == 'gates_status':
        status_text = f"┏━━━━━━━━━━━━━━━┓\n"
        status_text += f"┃    SYSTEM MONITOR - STATUS     ┃\n"
        status_text += f"┗━━━━━━━━━━━━━━━┛\n\n"
        status_text += f">> GATEWAY STATUS:\n"
        status_text += f"│  🔹 Stripe.......: 🟢 ONLINE\n"
        status_text += f"│  🟠 Amazon.......: 🟢 ONLINE\n"
        status_text += f"│  🔴 PayPal.......: 🟢 ONLINE\n"
        status_text += f"│  🟡 Ayden........: 🟢 ONLINE\n"
        status_text += f"│  🟢 Auth.........: 🟢 ONLINE\n"
        status_text += f"│  ⚫ CCN Charge...: 🟢 ONLINE\n"
        status_text += f"│  🤖 CyberSource..: 🟢 ONLINE [PREMIUM]\n"
        status_text += f"│  🇬🇧 Worldpay....: 🟢 ONLINE [PREMIUM]\n"
        status_text += f"│  🌐 Braintree....: 🟢 ONLINE [PREMIUM]\n\n"
        status_text += f">> SYSTEM INFO:\n"
        status_text += f"│  • Última sync...: {datetime.now().strftime('%H:%M:%S')}\n"
        status_text += f"│  • Uptime........: 99.9%\n"
        status_text += f"│  • Efectividad...: PRO\n\n"
        status_text += f"➤ Todos los gateways operativos"

        back_keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='gates_back')]]
        await query.edit_message_text(
            status_text,
            reply_markup=InlineKeyboardMarkup(back_keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if query.data == 'gates_back':
        keyboard = gate_system.create_gates_menu()
        user_data = db.get_user(user_id)

        # Verificar autorización con datos frescos
        gate_system.db.load_data()
        is_authorized = gate_system.is_authorized(user_id)
        is_founder = db.is_founder(user_id)
        is_cofounder = db.is_cofounder(user_id)
        is_moderator = db.is_moderator(user_id)
        is_premium = user_data.get('premium', False)

        # Verificar que el premium sea válido
        premium_valid = False
        if is_premium:
            premium_until = user_data.get('premium_until')
            if premium_until:
                try:
                    if isinstance(premium_until, str):
                        premium_until_date = datetime.fromisoformat(premium_until)
                    else:
                        premium_until_date = premium_until
                    premium_valid = datetime.now() < premium_until_date
                except:
                    premium_valid = True
            else:
                premium_valid = True

        # Determinar tipo de usuario y estado
        if is_founder:
            user_type = "👑 FUNDADOR"
            access_text = "✅ ACCESO COMPLETO"
            status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
            modules_status = "🔓"
            final_message = "➤ Selecciona gateway deseado:"
        elif is_cofounder:
            user_type = "💎 CO-FUNDADOR"
            access_text = "✅ ACCESO COMPLETO"
            status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
            modules_status = "🔓"
            final_message = "➤ Selecciona gateway deseado:"
        elif is_moderator:
            user_type = "🛡️ MODERADOR"
            access_text = "✅ ACCESO COMPLETO"
            status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
            modules_status = "🔓"
            final_message = "➤ Selecciona gateway deseado:"
        elif premium_valid:
            user_type = "💎 PREMIUM"
            access_text = "✅ ACCESO COMPLETO"
            status_section = "[✓] ACCESO TOTAL HABILITADO\n[✓] SISTEMAS OPERATIVOS"
            modules_status = "🔓"
            final_message = "➤ Selecciona gateway deseado:"
        else:
            user_type = "🆓 USUARIO ESTÁNDAR"
            access_text = "❌ SOLO VISTA PREVIA"
            status_section = "[!] ACCESO A FUNCIONES DENEGADO\n[!] VISUALIZACIÓN TEMPORAL ACTIVADA"
            modules_status = "🔒"
            final_message = "➤ Desbloquea acceso total:\n    ↳ PREMIUM ACTIVATION: @Laleyendas01"

        # Plantilla unificada
        response = f"┏━━━━━━━━━━━━━━━┓\n"
        response += f"┃    GATES CORE   -  DARK ACCESS     ┃\n"
        response += f"┗━━━━━━━━━━━━━━━┛\n\n"
        response += f"✘ USUARIO: {user_type}\n"
        response += f"✘ ESTADO : {access_text}\n"
        response += f"✘ CRÉDITOS DISPONIBLES: {user_data['credits']}\n"
        response += f"✘ COSTO POR GATE: 1 🔻\n"
        response += f"✘ MÓDULOS RESTRINGIDOS: {modules_status}\n\n"
        response += f"──────────────────────────────\n"
        response += f"{status_section}\n"
        response += f"──────────────────────────────\n\n"
        response += f">> GATES DISPONIBLES:\n"
        response += f"│  → 🔹 Stripe                    → 🟠 Amazon\n"
        response += f"│  → 🔴 PayPal                   → 🟡 Ayden\n"
        response += f"│  → 🟢 Auth                       → ⚫ CCN Charge\n"
        response += f"│  → 🤖 CyberSource AI\n"
        response += f"│  → 🌐 Braintree Pro       → 🇬🇧 Worldpay UK\n\n"
        response += f"{final_message}"

        await query.edit_message_text(
            response,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Procesar selección de gate específico
    gate_types = {
        'gate_stripe': ('Stripe Gate', '🔵'),
        'gate_amazon': ('Amazon Gate', '🟠'),
        'gate_paypal': ('PayPal Gate', '🔴'),
        'gate_ayden': ('Ayden Gate', '🟡'),
        'gate_auth': ('Auth Gate', '🟢'),
        'gate_ccn': ('CCN Charge', '⚫'),
        'gate_cybersource': ('CyberSource AI', '🤖'),
        'gate_worldpay': ('Worldpay UK', '🇬🇧'),
        'gate_braintree': ('Braintree Pro', '🌐')
    }

    if query.data in gate_types:
        # VERIFICAR PERMISOS AL SELECCIONAR GATE CON DATOS FRESCOS
        gate_system.db.load_data()  # FORZAR RECARGA ANTES DE VERIFICAR
        is_authorized = gate_system.is_authorized(user_id)

        # Log detallado para depuración con datos frescos
        user_data = db.get_user(user_id)
        logger.info(f"[GATE CALLBACK] Usuario {user_id}: authorized={is_authorized}, premium={user_data.get('premium', False)}, until={user_data.get('premium_until', 'None')}")

        if not is_authorized:
            await query.edit_message_text(
                "💻 SYSTEM SECURITY NODE 💻\n\n"
                "👤 USER STATUS: 🆓 FREE_MODE\n"
                "🛡 ACCESS LEVEL: 🚫 RESTRICTED\n"
                "📅 PREMIUM VALID UNTIL: ❌ NONE\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "⚠ ERROR 403: ACCESS DENIED ⚠\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "🔒 RESTRICTED MODULES\n\n"
                "🗡 Gates Avanzados OFF\n"
                "🚀 Procesamiento PRO OFF\n"
                "🛡 Anti-Rate Limit OFF\n\n"
                "💎 PREMIUM MODULES\n\n"
                "🗡 Gates Avanzados ON\n"
                "🎯 Efectividad PRO ON\n"
                "🤝 Soporte Prioritario\n"
                "📦 Multi-Card Process\n"
                "♾ Sin Límite de Uso\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📩 CONTACT ADMIN: @Laleyendas01\n"
                "━━━━━━━━━━━━━━━━━━━━")
            return

        gate_name, gate_emoji = gate_types[query.data]

        # Crear sesión para este usuario (solo si está autorizado)
        gate_system.active_sessions[user_id] = {
            'gate_type': query.data,
            'gate_name': gate_name,
            'gate_emoji': gate_emoji,
            'timestamp': datetime.now()
        }

        response = f"┏━━━━━━━━━━━━━━━┓\n"
        response += f"┃    {gate_name.upper()} - DARK PROCESS     ┃\n"
        response += f"┗━━━━━━━━━━━━━━━┛\n\n"
        response += f">> GATEWAY INFO:\n"
        response += f"│  • Estado........: 🟢 ONLINE\n"
        response += f"│  • Precio........: 5 créditos/tarjeta\n"
        response += f"│  • Plan..........: Premium Access\n"
        response += f"│  • Comando.......: /am\n\n"
        response += f">> FORMAT REQUIRED:\n"
        response += f"│  → 4532123456781234|12|25|123\n\n"
        response += f">> PROCESS INFO:\n"
        response += f"│  • Auto-processing: ✅\n"
        response += f"│  • Tiempo estimado: 2-5s\n"
        response += f"│  • Efectividad....: PRO\n\n"
        response += f"──────────────────────────────\n"
        response += f"[!] Sistema listo para procesar\n"
        response += f"──────────────────────────────\n\n"
        response += f"➤ Envía tu tarjeta para procesar"

        back_keyboard = [[InlineKeyboardButton("🔙 Volver al menú", callback_data='gates_back')]]

        await query.edit_message_text(
            response,
            reply_markup=InlineKeyboardMarkup(back_keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def process_gate_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesar múltiples tarjetas enviadas cuando hay sesión activa - CON CONTROL DE RATE LIMITING"""
    global gate_system
    user_id = str(update.effective_user.id)

    # Importar db aquí para asegurar que tenemos la instancia actual
    from telegram_bot import db as current_db
    if gate_system is not None:
        gate_system.db = current_db

    # Verificar si hay sesión activa primero
    if user_id not in gate_system.active_sessions:
        return

    session = gate_system.active_sessions[user_id]
    message_text = update.message.text.strip()

    # Detectar múltiples tarjetas en el mensaje
    import re
    card_pattern = r'\b\d{13,19}\|\d{1,2}\|\d{2,4}\|\d{3,4}\b'
    cards_found = re.findall(card_pattern, message_text)

    if not cards_found:
        await update.message.reply_text(
            "❌ **Formato inválido**\n\n"
            "💡 **Formato correcto:**\n"
            "`4532123456781234|12|25|123`\n\n"
            "📋 **Puedes enviar múltiples tarjetas separadas por líneas**",
            parse_mode=ParseMode.MARKDOWN)
        return

    # Verificar límites según nivel de usuario
    is_founder = db.is_founder(user_id)
    is_cofounder = db.is_cofounder(user_id)
    user_data = db.get_user(user_id)
    is_premium = user_data.get('premium', False)

    # Establecer límites
    if is_founder:
        max_cards = 15  # Fundadores más tarjetas
        user_type = "👑 FUNDADOR"
    elif is_cofounder:
        max_cards = 15  # Co-fundadores también más
        user_type = "💎 CO-FUNDADOR"
    elif is_premium:
        max_cards = 15   # Premium moderado
        user_type = "💎 PREMIUM"
    else:
        await update.message.reply_text("❌ Acceso denegado")
        return

    # Verificar límite de tarjetas
    if len(cards_found) > max_cards:
        await update.message.reply_text(
            f"❌ **LÍMITE EXCEDIDO** ❌\n\n"
            f"🎯 **Tu nivel:** {user_type}\n"
            f"📊 **Límite máximo:** {max_cards} tarjetas\n"
            f"📤 **Enviaste:** {len(cards_found)} tarjetas\n\n"
            f"💡 **Envía máximo {max_cards} tarjetas por vez**",
            parse_mode=ParseMode.MARKDOWN)
        return

    # Verificar créditos (5 por tarjeta)
    total_cost = len(cards_found) * 5
    if user_data['credits'] < total_cost:
        await update.message.reply_text(
            f"❌ **LOOT INSUFICIENTE** ❌\n\n"
            f"💰 **Necesitas:** {total_cost} loot\n"
            f"💳 **Tienes:** {user_data['credits']} loot\n"
            f"📊 **Costo:** 5 loot por tarjeta\n"
            f"🎯 **Tarjetas:** {len(cards_found)}\n\n"
            f"💡 Usa `/loot` para obtener loot gratis",
            parse_mode=ParseMode.MARKDOWN)
        return

    # NO descontar todos los créditos al inicio - se descontarán individualmente

    # Procesar cada tarjeta individualmente CON CONTROL DE RATE LIMITING
    results = [] # Guardar resultados para estadísticas
    for i, card_data in enumerate(cards_found, 1):

        # Descontar 1 créditos por esta tarjeta específica
        current_user_data = db.get_user(user_id)
        if current_user_data['credits'] >= 1:
            db.update_user(user_id, {'credits': current_user_data['credits'] - 1})
        else:
            # Si no hay suficientes créditos para esta tarjeta, parar el procesamiento
            await update.message.reply_text(
                f"❌ **LOOT INSUFICIENTE** ❌\n\n"
                f"💰 **Se necesitan 5 loot más para la tarjeta {i}/{len(cards_found)}**\n"
                f"💳 **Loot actual:** {current_user_data['credits']}\n\n"
                f"⚠️ **Procesamiento detenido en tarjeta {i-1}/{len(cards_found)}**",
                parse_mode=ParseMode.MARKDOWN)
            break

        # Mensaje de procesamiento
        processing_msg = await update.message.reply_text(
            f"╔═[ {session['gate_emoji']} {session['gate_name'].upper()} - INICIANDO ]═╗\n"
            f"║ 💳 Tarjeta: [{i}/{len(cards_found)}] {card_data[:4]}****{card_data[-4:]} ║\n"
            f"║ ⏳ Estado : Conectando al gateway...    \n"
            f"║ 🔄 Progreso: [██░░░░░░░░] 20%           \n"
            f"║ 📡 Latencia: Calculando...              \n"
            f"╚════════════════════════╝",
            parse_mode=ParseMode.MARKDOWN
        )

        # CONTROLAR RATE LIMITING - Esperar entre mensajes
        if i > 1:
            await asyncio.sleep(3)  # Pausa entre tarjetas

        # Simular progreso CON CONTROL DE RATE LIMITING
        await asyncio.sleep(1.5)
        await gate_system.safe_edit_message(
            processing_msg,
            f"╔═[ {session['gate_emoji']} {session['gate_name'].upper()} - VERIFICANDO ]═╗\n"
            f"║ 💳 Tarjeta: [{i}/{len(cards_found)}] {card_data[:4]}****{card_data[-4:]} ║\n"
            f"║ ⏳ Estado : Validando datos...          \n"
            f"║ 🔄 Progreso: [████░░░░░░] 40%           \n"
            f"║ 📡 Latencia: 0.234s                    \n"
            f"╚════════════════════════╝"
        )

        await asyncio.sleep(1.5)
        await gate_system.safe_edit_message(
            processing_msg,
            f"╔═[ {session['gate_emoji']} {session['gate_name'].upper()} - PROCESANDO ]═╗\n"
            f"║ 💳 Tarjeta: [{i}/{len(cards_found)}] {card_data[:4]}****{card_data[-4:]} ║\n"
            f"║ ⏳ Estado : Enviando al gateway...      \n"
            f"║ 🔄 Progreso: [██████░░░░] 60%           \n"
            f"║ 📡 Latencia: 0.456s                    \n"
            f"╚════════════════════════╝"
        )

        # Procesar según el tipo de gate
        gate_type = session['gate_type']
        if gate_type == 'gate_stripe':
            result = await gate_system.process_stripe_gate(card_data)
        elif gate_type == 'gate_amazon':
            result = await gate_system.process_amazon_gate(card_data)
        elif gate_type == 'gate_paypal':
            result = await gate_system.process_paypal_gate(card_data)
        elif gate_type == 'gate_ayden':
            result = await gate_system.process_ayden_gate(card_data)
        elif gate_type == 'gate_ccn':
            result = await gate_system.process_ccn_charge(card_data)
        elif gate_type == 'gate_cybersource':
            result = await gate_system.process_cybersource_ai(card_data)
        elif gate_type == 'gate_worldpay':
            result = await gate_system.process_worldpay_gate(card_data)
        elif gate_type == 'gate_braintree':
            result = await gate_system.process_braintree_gate(card_data)
        else:
            result = await gate_system.process_auth_gate(card_data)

        results.append(result) # Agregar resultado para estadísticas

        # Mostrar resultado final con nuevo formato
        parts = card_data.split('|')
        card_number = parts[0] if len(parts) > 0 else 'N/A'
        exp_date = f"{parts[1]}/{parts[2]}" if len(parts) > 2 else 'N/A'

        # Obtener emoji del gate
        gate_emoji = session['gate_emoji']
        gate_name = session['gate_name'].upper()

        # Obtener créditos actualizados DESPUÉS de cada verificación individual
        current_user_data = db.get_user(user_id)
        credits_remaining = current_user_data['credits']

        final_response = f"╔═[ {gate_emoji} {gate_name}: RESULTADO ]═╗\n"
        final_response += f"║ 💳 Tarjeta : {card_number}\n"
        final_response += f"║ 📅 Expira : {exp_date}\n"
        final_response += f"║ 🎯 Estado : {result['status']}\n"
        final_response += f"║ 📡 Gateway : {result['gateway']}\n"
        final_response += f"║ 💰 Monto : {result.get('amount', '$0.00')}\n"
        final_response += f"║ 📝 Respuesta : {result['message']}\n"
        final_response += f"║ ⏰ Tiempo : {datetime.now().strftime('%H:%M:%S')}\n"
        final_response += f"║ 👤 Checker : @{update.effective_user.username or update.effective_user.first_name}\n"
        final_response += f"║ 🔢 Proceso : {i} / {len(cards_found)}\n"
        final_response += f"╚════════════════════════════════╝\n\n"

        final_response += f"💰 loot restantes → {credits_remaining}\n\n"

        # System notice según el resultado
        if result['success']:
            final_response += f"✅ SYSTEM NOTICE:\n"
            final_response += f"• Transacción aprobada por el gateway\n"
            final_response += f"• Método de pago válido y activo"
        else:
            final_response += f"⚠️ SYSTEM NOTICE:\n"
            final_response += f"• Transacción rechazada por el gateway\n"
            final_response += f"• Método de pago no válido"


        keyboard = [[InlineKeyboardButton("🔄 Procesar otra", callback_data=gate_type),
                    InlineKeyboardButton("🔙 Menú principal", callback_data='gates_back')]]

        await gate_system.safe_edit_message(
            processing_msg,
            final_response,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # Pausa adicional entre tarjetas para evitar rate limiting
        if i < len(cards_found):
            await asyncio.sleep(2)

    # Sistema de estadísticas avanzadas con analytics
    try:
        # Contar éxitos por gateway para estadísticas
        gateway_stats = {}
        for result in results:
            gateway = result['gateway']
            if gateway not in gateway_stats:
                gateway_stats[gateway] = {'success': 0, 'total': 0}
            gateway_stats[gateway]['total'] += 1
            if result['is_live']:
                gateway_stats[gateway]['success'] += 1

        # Actualizar estadísticas del usuario
        current_stats = db.get_user(user_id)
        new_stats = {
            'total_checked': current_stats['total_checked'] + len(cards_found)
        }

        # Agregar estadísticas por gateway si no existen
        if 'gateway_stats' not in current_stats:
            current_stats['gateway_stats'] = {}

        # Actualizar stats por gateway
        for gateway, stats in gateway_stats.items():
            if gateway not in current_stats['gateway_stats']:
                current_stats['gateway_stats'][gateway] = {'success': 0, 'total': 0}
            current_stats['gateway_stats'][gateway]['success'] += stats['success']
            current_stats['gateway_stats'][gateway]['total'] += stats['total']

        new_stats['gateway_stats'] = current_stats['gateway_stats']
        db.update_user(user_id, new_stats)

    except Exception as e:
        logger.error(f"❌ Error actualizando estadísticas: {e}")
        # Continuar sin actualizar estadísticas si hay error


    # Limpiar sesión al final
    if user_id in gate_system.active_sessions:
        del gate_system.active_sessions[user_id]
def check_user_premium_status(user_id: str) -> dict:
    """Función de verificación rápida del estado premium - SOLO PARA TESTING"""
    try:
        user_data = db.get_user(user_id)
        is_premium = user_data.get('premium', False)
        premium_until = user_data.get('premium_until')

        return {
            'user_id': user_id,
            'is_premium': is_premium,
            'premium_until': premium_until,
            'is_founder': db.is_founder(user_id),
            'is_cofounder': db.is_cofounder(user_id),
            'is_moderator': db.is_moderator(user_id),
            'authorized_for_gates': gate_system.is_authorized(user_id) if gate_system else False
        }
    except Exception as e:
        return {'error': str(e)}

async def is_authorized(user_id: str, premium_required: bool = False) -> tuple[bool, str]:
    """
    Verifica si el usuario está autorizado para usar los gates
    Returns: (is_authorized, status_message)
    """
    try:
        # Verificar admin primero
        if int(user_id) in ADMIN_IDS:
            return True, "👑 ADMIN"

        # Verificar roles de staff desde la base de datos
        if db.is_founder(user_id):
            return True, "👑 FUNDADOR"

        if db.is_cofounder(user_id):
            return True, "💎 CO-FUNDADOR"

        if db.is_moderator(user_id):
            return True, "🛡️ MODERADOR"

        # CORRECCIÓN: Obtener datos del usuario y verificar premium
        user_data = db.get_user(user_id)

        # Forzar verificación de premium desde la base de datos
        is_premium = user_data.get('premium', False)
        premium_until = user_data.get('premium_until')

        logger.info(f"Verificando usuario {user_id}: premium={is_premium}, until={premium_until}")

        if is_premium and premium_until:
            try:
                premium_until_date = datetime.fromisoformat(premium_until)
                if datetime.now() < premium_until_date:
                    logger.info(f"Usuario {user_id} tiene premium válido hasta {premium_until_date}")
                    return True, "💎 PREMIUM"
                else:
                    # Premium expirado
                    logger.info(f"Premium de usuario {user_id} expirado")
                    db.update_user(user_id, {'premium': False, 'premium_until': None})
                    return False, "❌ PREMIUM EXPIRADO"
            except Exception as date_error:
                logger.error(f"Error parsing fecha premium para {user_id}: {date_error}")
                return False, "❌ ERROR PREMIUM"
        elif is_premium and not premium_until:
            # Premium permanente
            logger.info(f"Usuario {user_id} tiene premium permanente")
            return True, "💎 PREMIUM"

        # Usuario estándar
        logger.info(f"Usuario {user_id} es estándar")
        if premium_required:
            return False, "❌ REQUIERE PREMIUM"
        else:
            return True, "✅ USUARIO ESTÁNDAR"

    except Exception as e:
        logger.error(f"Error en verificación de autorización para {user_id}: {e}")
        return False, "❌ ERROR DEL SISTEMA"

# Función de prueba para verificar tasas realistas del sistema
async def test_realistic_rates(num_tests: int = 50) -> dict:
    """
    Probar el sistema con múltiples tarjetas para verificar tasas realistas.
    Esperamos máximo 5% de éxito (idealmente 0.5-2%).
    """
    print(f"\n🧪 PROBANDO SISTEMA ULTRA REALISTA - {num_tests} verificaciones")
    
    # Tarjetas de prueba válidas (Luhn válido, fechas futuras)
    test_cards = [
        "4532123456781234|12|26|123",  # Visa
        "5555555555554444|01|27|456",  # Mastercard  
        "4000000000000002|03|28|789",  # Visa
        "5105105105105100|06|29|321",  # Mastercard
        "4111111111111111|08|25|654",  # Visa
        "5200828282828210|11|26|987",  # Mastercard
        "4012888888881881|02|27|147",  # Visa
    ]
    
    results = {
        'stripe': {'live': 0, 'dead': 0, 'total': 0},
        'amazon': {'live': 0, 'dead': 0, 'total': 0}, 
        'paypal': {'live': 0, 'dead': 0, 'total': 0},
        'overall': {'live': 0, 'dead': 0, 'total': 0}
    }
    
    gate_system = GateSystem()
    user_id = "test_user_realism_check"
    
    for i in range(num_tests):
        card = random.choice(test_cards)
        
        # Probar cada gateway
        stripe_result = await gate_system.process_stripe_gate(card, user_id)
        amazon_result = await gate_system.process_amazon_gate(card, user_id)
        paypal_result = await gate_system.process_paypal_gate(card, user_id)
        
        # Contar resultados
        for gateway, result in [('stripe', stripe_result), ('amazon', amazon_result), ('paypal', paypal_result)]:
            results[gateway]['total'] += 1
            results['overall']['total'] += 1
            
            if result.get('is_live', False):
                results[gateway]['live'] += 1
                results['overall']['live'] += 1
            else:
                results[gateway]['dead'] += 1
                results['overall']['dead'] += 1
        
        # Progreso cada 10 pruebas
        if (i + 1) % 10 == 0:
            print(f"   Progreso: {i + 1}/{num_tests} pruebas completadas")
    
    # Calcular estadísticas finales
    print(f"\n📊 RESULTADOS FINALES:")
    print(f"{'=' * 50}")
    
    for gateway, data in results.items():
        if data['total'] > 0:
            live_rate = (data['live'] / data['total']) * 100
            color = "✅" if live_rate <= 5.0 else "❌"
            print(f"{color} {gateway.upper()}: {data['live']}/{data['total']} live ({live_rate:.1f}%)")
    
    overall_rate = (results['overall']['live'] / results['overall']['total']) * 100
    overall_color = "✅" if overall_rate <= 5.0 else "❌"
    print(f"\n{overall_color} GENERAL: {results['overall']['live']}/{results['overall']['total']} live ({overall_rate:.1f}%)")
    
    # Evaluación del sistema
    if overall_rate <= 2.0:
        print(f"🎯 EXCELENTE: Tasas ultra realistas ({overall_rate:.1f}% ≤ 2%)")
    elif overall_rate <= 5.0:
        print(f"✅ BUENO: Tasas realistas ({overall_rate:.1f}% ≤ 5%)")
    else:
        print(f"❌ PROBLEMA: Tasas demasiado altas ({overall_rate:.1f}% > 5%)")
    
    print(f"{'=' * 50}\n")
    
    return {
        'success': overall_rate <= 5.0,
        'overall_live_rate': overall_rate,
        'results': results,
        'evaluation': 'excellent' if overall_rate <= 2.0 else 'good' if overall_rate <= 5.0 else 'needs_adjustment'
    }
