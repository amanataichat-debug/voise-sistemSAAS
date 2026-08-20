#!/usr/bin/env python3
"""
🔍 Gemini Live API Event Inspector
Запусти в Render Shell для просмотра полной структуры событий

Использование:
    python test_gemini_events.py
"""

import asyncio
import json
import websockets

# API ключ
API_KEY = "AIzaSyCi9L68J5e-q_KjoRGg3MCy324B7_EsNOE"

# Актуальная модель Gemini Live API (декабрь 2025)
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

# WebSocket URL
WS_URL = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={API_KEY}"


async def test_gemini_events():
    """
    Подключается к Gemini Live API и выводит ВСЕ события в полном виде
    """
    
    print("=" * 60)
    print("🔍 GEMINI LIVE API EVENT INSPECTOR")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"API Key: {API_KEY[:15]}...{API_KEY[-5:]}")
    print("=" * 60)
    
    try:
        async with websockets.connect(WS_URL) as ws:
            print("✅ WebSocket connected!")
            
            # 1. Отправляем setup с включённой транскрипцией
            setup_message = {
                "setup": {
                    "model": f"models/{MODEL}",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {
                                    "voiceName": "Aoede"
                                }
                            }
                        }
                    },
                    "systemInstruction": {
                        "parts": [{"text": "Ты голосовой ассистент. Отвечай кратко на русском языке."}]
                    },
                    # Включаем транскрипцию (оба варианта написания)
                    "inputAudioTranscription": {},
                    "outputAudioTranscription": {}
                }
            }
            
            print("\n📤 Sending setup...")
            await ws.send(json.dumps(setup_message))
            
            # 2. Ждём setupComplete
            print("\n⏳ Waiting for setupComplete...")
            
            event_count = 0
            setup_complete = False
            
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    event_count += 1
                    
                    try:
                        data = json.loads(raw)
                    except:
                        print(f"\n❓ Event #{event_count}: Non-JSON data ({len(raw)} bytes)")
                        continue
                    
                    # Красивый вывод события
                    print("\n" + "=" * 60)
                    print(f"📡 EVENT #{event_count}")
                    print("=" * 60)
                    print(f"TOP-LEVEL KEYS: {list(data.keys())}")
                    
                    # Подробный вывод каждого типа события
                    if "setupComplete" in data:
                        print("✅ SETUP COMPLETE!")
                        print(json.dumps(data["setupComplete"], indent=2, ensure_ascii=False))
                        setup_complete = True
                        
                        # Отправляем текстовое сообщение для теста
                        print("\n📤 Sending test message: 'Привет! Расскажи кратко о себе.'")
                        test_message = {
                            "clientContent": {
                                "turns": [{
                                    "role": "user",
                                    "parts": [{"text": "Привет! Расскажи кратко о себе."}]
                                }],
                                "turnComplete": True
                            }
                        }
                        await ws.send(json.dumps(test_message))
                        print("✅ Test message sent!")
                    
                    elif "serverContent" in data:
                        sc = data["serverContent"]
                        print("📦 SERVER CONTENT:")
                        print(f"   serverContent KEYS: {list(sc.keys())}")
                        
                        # Транскрипции внутри serverContent
                        if "inputTranscription" in sc:
                            print(f"\n   👤 INPUT TRANSCRIPTION (inside serverContent):")
                            print(f"      FULL: {json.dumps(sc['inputTranscription'], ensure_ascii=False)}")
                            it = sc['inputTranscription']
                            print(f"      keys: {list(it.keys())}")
                            if 'text' in it:
                                print(f"      text: '{it['text']}'")
                            if 'finished' in it:
                                print(f"      finished: {it['finished']} ⭐")
                        
                        if "outputTranscription" in sc:
                            print(f"\n   🤖 OUTPUT TRANSCRIPTION (inside serverContent):")
                            print(f"      FULL: {json.dumps(sc['outputTranscription'], ensure_ascii=False)}")
                            ot = sc['outputTranscription']
                            print(f"      keys: {list(ot.keys())}")
                            if 'text' in ot:
                                print(f"      text: '{ot['text']}'")
                            if 'finished' in ot:
                                print(f"      finished: {ot['finished']} ⭐")
                        
                        # Model turn
                        if "modelTurn" in sc:
                            mt = sc["modelTurn"]
                            parts = mt.get("parts", [])
                            print(f"\n   🎭 MODEL TURN ({len(parts)} parts):")
                            for i, part in enumerate(parts[:3]):  # Показываем первые 3
                                part_keys = list(part.keys())
                                print(f"      Part {i}: keys={part_keys}")
                                if "text" in part:
                                    text = part['text']
                                    print(f"         text: '{text[:100]}{'...' if len(text) > 100 else ''}'")
                                if "inlineData" in part:
                                    mime = part["inlineData"].get("mimeType", "?")
                                    data_len = len(part["inlineData"].get("data", ""))
                                    print(f"         inlineData: {mime}, {data_len} bytes")
                            if len(parts) > 3:
                                print(f"      ... and {len(parts) - 3} more parts")
                        
                        # Turn complete
                        if sc.get("turnComplete"):
                            print(f"\n   🏁 turnComplete = True ⭐⭐⭐")
                        
                        if sc.get("interrupted"):
                            print(f"\n   ⚡ interrupted = True")
                        
                        if sc.get("generationComplete"):
                            print(f"\n   ✅ generationComplete = True")
                    
                    # Top-level транскрипции (отдельные сообщения)
                    elif "inputTranscription" in data:
                        print("👤 TOP-LEVEL INPUT TRANSCRIPTION:")
                        print(f"   FULL: {json.dumps(data['inputTranscription'], indent=2, ensure_ascii=False)}")
                        it = data['inputTranscription']
                        print(f"   keys: {list(it.keys())}")
                        if 'finished' in it:
                            print(f"   finished: {it['finished']} ⭐⭐⭐")
                    
                    elif "outputTranscription" in data:
                        print("🤖 TOP-LEVEL OUTPUT TRANSCRIPTION:")
                        print(f"   FULL: {json.dumps(data['outputTranscription'], indent=2, ensure_ascii=False)}")
                        ot = data['outputTranscription']
                        print(f"   keys: {list(ot.keys())}")
                        if 'finished' in ot:
                            print(f"   finished: {ot['finished']} ⭐⭐⭐")
                    
                    elif "toolCall" in data:
                        print("🔧 TOOL CALL:")
                        print(json.dumps(data["toolCall"], indent=2, ensure_ascii=False))
                    
                    elif "usageMetadata" in data:
                        print("📊 USAGE METADATA:")
                        print(json.dumps(data["usageMetadata"], indent=2, ensure_ascii=False))
                    
                    else:
                        # Неизвестный тип события - выводим полностью
                        print("❓ OTHER EVENT:")
                        raw_str = json.dumps(data, ensure_ascii=False)
                        if len(raw_str) > 500:
                            print(f"   {raw_str[:500]}...")
                        else:
                            print(f"   {raw_str}")
                    
                    # Проверяем turnComplete для завершения
                    if "serverContent" in data and data["serverContent"].get("turnComplete"):
                        print("\n" + "=" * 60)
                        print("🏁 TURN COMPLETE - Ответ завершён!")
                        print("=" * 60)
                        
                        # Продолжаем слушать ещё немного для доп. событий
                        print("\n⏳ Waiting 5 sec for post-turn events (like finished transcriptions)...")
                        try:
                            while True:
                                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                                event_count += 1
                                data = json.loads(raw)
                                print(f"\n📡 POST-TURN EVENT #{event_count}:")
                                print(f"   Keys: {list(data.keys())}")
                                
                                if "outputTranscription" in data:
                                    ot = data['outputTranscription']
                                    print(f"   🤖 OUTPUT_TRANS: {json.dumps(ot, ensure_ascii=False)}")
                                    if 'finished' in ot:
                                        print(f"   ⭐ finished={ot['finished']}")
                                elif "inputTranscription" in data:
                                    it = data['inputTranscription']
                                    print(f"   👤 INPUT_TRANS: {json.dumps(it, ensure_ascii=False)}")
                                    if 'finished' in it:
                                        print(f"   ⭐ finished={it['finished']}")
                                elif "serverContent" in data:
                                    sc = data["serverContent"]
                                    print(f"   serverContent keys: {list(sc.keys())}")
                                    if "outputTranscription" in sc:
                                        ot = sc['outputTranscription']
                                        print(f"   🤖 SC.OUTPUT_TRANS: {json.dumps(ot, ensure_ascii=False)}")
                                        if 'finished' in ot:
                                            print(f"   ⭐ finished={ot['finished']}")
                                    if "inputTranscription" in sc:
                                        it = sc['inputTranscription']
                                        print(f"   👤 SC.INPUT_TRANS: {json.dumps(it, ensure_ascii=False)}")
                                        if 'finished' in it:
                                            print(f"   ⭐ finished={it['finished']}")
                                else:
                                    print(f"   Raw: {json.dumps(data, ensure_ascii=False)[:300]}")
                        except asyncio.TimeoutError:
                            print("   (no more events after 5 sec)")
                        
                        break
                    
                except asyncio.TimeoutError:
                    print("\n⏰ Timeout waiting for events")
                    break
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"\n🔌 Connection closed: {e}")
                    break
            
            print("\n" + "=" * 60)
            print(f"📊 TOTAL EVENTS: {event_count}")
            print("=" * 60)
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("\n🚀 Starting Gemini Event Inspector...\n")
    asyncio.run(test_gemini_events())
