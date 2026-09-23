import React from 'react';
import Icon from './Icon';

// Голосовые модели платформы. Для Grok и ElevenLabs своих логотипов в
// /static/icons/models/ нет — вместо картинки рисуется иконка из спрайта.
export const MODELS = {
  openai: { file: 'openai.svg', name: 'OpenAI' },
  gemini: { file: 'gemini-color.svg', name: 'Gemini' },
  grok: { icon: 'zap', name: 'Grok' },
  fish: { file: 'fishaudio-color.svg', name: 'Fish Audio' },
  elevenlabs: { icon: 'audio-lines', name: 'ElevenLabs' },
};

export const MODEL_ORDER = ['openai', 'gemini', 'grok', 'fish', 'elevenlabs'];

// Модели на ключах платформы (остальные работают на ключе пользователя)
export const PLATFORM_KEY_MODELS = ['fish'];

function ModelLogo({ code, size = 22, wrap = true }) {
  const model = MODELS[code];
  if (!model) return null;

  const style = { width: size, height: size };
  const inner = model.file ? (
    <img
      className={`logo${model.file.endsWith('-color.svg') ? ' logo-color' : ''}`}
      src={`/static/icons/models/${model.file}`}
      alt=""
      style={style}
    />
  ) : (
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
