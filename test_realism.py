#!/usr/bin/env python3
"""
Script de prueba para verificar que el sistema CC Checker 
tenga tasas ultra realistas (máximo 5%).
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from gates_system import test_realistic_rates

async def main():
    """Ejecutar prueba del sistema ultra realista."""
    print("🚀 INICIANDO PRUEBA DEL SISTEMA CC CHECKER ULTRA REALISTA")
    print("=" * 60)
    print("📋 OBJETIVO: Verificar tasas de éxito ≤ 5% (idealmente ≤ 2%)")
    print("📋 PROBLEMA ORIGINAL: Todas las tarjetas salían como 'live'")
    print("📋 SOLUCIÓN: Algoritmo ultra realista con base 0.5%")
    print("=" * 60)
    
    # Ejecutar prueba con 30 verificaciones (90 pruebas totales: 30 x 3 gates)
    result = await test_realistic_rates(num_tests=30)
    
    print(f"\n🎯 RESULTADO FINAL:")
    if result['evaluation'] == 'excellent':
        print(f"🏆 SISTEMA EXCELENTE - Tasas ultra realistas!")
        print(f"   El problema original está completamente resuelto.")
    elif result['evaluation'] == 'good':
        print(f"✅ SISTEMA BUENO - Tasas realistas alcanzadas!")
        print(f"   Mejora significativa vs. sistema anterior.")
    else:
        print(f"❌ SISTEMA REQUIERE AJUSTES - Tasas aún altas.")
        print(f"   Necesita optimización adicional.")
    
    print(f"\n📊 COMPARACIÓN:")
    print(f"   Antes: ~65% éxito (todas las tarjetas 'live')")
    print(f"   Ahora: {result['overall_live_rate']:.1f}% éxito")
    print(f"   Mejora: -{(65 - result['overall_live_rate']):.1f} puntos porcentuales")
    
    return result

if __name__ == "__main__":
    asyncio.run(main())