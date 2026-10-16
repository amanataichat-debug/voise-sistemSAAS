/* ============================================================================
 * agent/files.js — файлы агента на странице агента → /api/agent/files
 *
 * Библиотека файлов целиком переехала на страницу «Знания агента»
 * (/static/agent-knowledge.html, ветка 1610-kb-calls): там она в одном списке
 * со знаниями, и у каждого файла два переключателя — «агент отправляет
 * клиентам» и «агент ищет ответы внутри». Раньше это были две соседние
 * модалки, и один и тот же файл приходилось заводить дважды.
 *
 * Здесь осталось то, что нужно самой странице агента:
 *   • loadAgentFiles      — счётчик файлов для карточки «Знания и файлы»
 *   • downloadAgentFile   — скачивание по подписанной ссылке; вызывается из
 *                           карточки контакта (вложения клиента) и из раздела
 *                           фирменного стиля (просмотр логотипа)
 *
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

let agentFilesState = { library: [], storage: true, maxMb: 20 };

const AGENT_FILE_ERRORS = {
  file_too_large: 'файл слишком большой',
  too_long: 'слишком длинное',
  insufficient_credits: 'не обработан — закончились кредиты',
  unsupported_format: 'формат не поддерживается',
  transcription_failed: 'не удалось распознать речь',
  no_text: 'текста не найдено',
  download_failed: 'не удалось скачать',
  storage_not_configured: 'хранилище файлов не настроено на сервере',
  library_limit_reached: 'в библиотеке уже 100 файлов',
  empty_file: 'пустой файл',
};

async function loadAgentFiles(){
  try{
    const r = await apiFetch(API + '/files?source=library');
    if(!r || r.status !== 200){ agentFilesState.library = []; renderKnowledgeBaseBlock(); return; }
    const data = await r.json();
    agentFilesState.library = data.files || [];
    agentFilesState.storage = data.storage_configured !== false;
    agentFilesState.maxMb = data.max_mb || 20;
  }catch(e){ agentFilesState.library = []; }
  // Файлы — часть карточки «Знания и файлы», своего блока у них больше нет.
  if(typeof renderKnowledgeBaseBlock === 'function') renderKnowledgeBaseBlock();
}

/**
 * Скачать файл агента: берём подписанную ссылку и открываем её.
 * Используется карточкой контакта (вложения клиента) и фирменным стилем.
 */
async function downloadAgentFile(fileId){
  try{
    const r = await apiFetch(API + '/files/' + encodeURIComponent(fileId) + '/link');
    if(!r || r.status !== 200){
      const err = r ? await r.json().catch(() => ({})) : {};
      showToast(errText(err.detail ?? err.message) || 'Не удалось получить ссылку', 'error');
      return;
    }
    const data = await r.json();
    if(data.url) window.open(data.url, '_blank', 'noopener');
  }catch(e){
    showToast('Не удалось скачать файл', 'error');
  }
}
