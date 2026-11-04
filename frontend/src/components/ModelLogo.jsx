import React from 'react';
import Icon from './Icon';

// Голосовые движки платформы (white-label): клиент видит только наши названия.
// Под капотом: Voksy Voice — каскад распознавание → текстовая модель → синтез
// (ElevenLabs), Voksy Live — речь в речь (GPT-Live), Voksy Live Go — Gemini Live.
// Старые коды (openai, gemini, grok, fish, elevenlabs) оставлены как синонимы,
// чтобы демо-сцены и макеты не ломались. Логотипов провайдеров не показываем.
const VOICE = { icon: 'audio-lines', name: 'Voksy Voice' };
const LIVE = { icon: 'zap', name: 'Voksy Live' };
const LIVE_GO = { icon: 'sparkles', name: 'Voksy Live Go' };

export const MODELS = {
  voice: VOICE,
  live: LIVE,
  livego: LIVE_GO,
  elevenlabs: VOICE,
  fish: VOICE,
  openai: LIVE,
  grok: LIVE,
  gemini: LIVE_GO,
};

// То, что можно выбрать в кабинете
export const MODEL_ORDER = ['voice', 'live'];

// Все движки работают на ключах платформы
export const PLATFORM_KEY_MODELS = ['voice', 'live'];

function ModelLogo({ code, size = 22, wrap = true }) {
  const model = MODELS[code];
  if (!model) return null;

  const style = { width: size, height: size };
  const inner = (
    <span className="logo logo-ic" style={style} aria-hidden="true">
      <Icon name={model.icon} />
    </span>
  );

  if (!wrap) return inner;
  return (
    <span className="logo-wrap" style={{ width: size + 14, height: size + 14 }}>
      {inner}
    </span>
  );
}

export default ModelLogo;
