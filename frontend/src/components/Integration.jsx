import React, { useState } from 'react';
import { useT } from '../i18n';
import Icon from './Icon';
import SectionHead from './SectionHead';
import { Reveal, Parallax } from './Reveal';
import { SITE_URL, copyText } from './contacts';

// Пример кода виджета — тот же формат, что генерирует кабинет (data-assistantId, data-server).
// Живой демо-виджет на лендинге не подключаем (своего демо-ассистента пока нет).
function widgetCode(assistantId) {
  return `<!-- VoksiAI Voice Assistant -->
<script>
  (function () {
    var script = document.createElement('script');
    script.src = '${SITE_URL}/static/gemini-widget.js';
    script.setAttribute('data-assistantId', '${assistantId}');
    script.setAttribute('data-server', '${SITE_URL}');
    script.setAttribute('data-position', 'bottom-right');
    script.async = true;
    document.head.appendChild(script);
  })();
</script>
<!-- End VoksiAI Widget -->`;
}

function Integration() {
  const { t } = useT();
  const [copied, setCopied] = useState(false);
  const code = widgetCode(t('integration.code_assistant_id'));

  const onCopy = () => {
    copyText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="sec sec-tint tint-blue" id="integration">
      <div className="lp-container">
        <SectionHead index="04" title={t('integration.title')} lead={t('integration.lead')} />
        <div className="integ">
          <Parallax amount={24}>
            <Reveal className="code" y={20}>
              <div className="code-head">
                <span className="code-file">
                  <Icon name="code" className="ic-sm" />
                  {t('integration.code_file')}
                </span>
                <button type="button" className="btn btn-sm btn-ghost" onClick={onCopy}>
                  <Icon name={copied ? 'check' : 'copy'} className="ic-sm" />
                  {copied ? t('integration.copied') : t('integration.copy')}
                </button>
              </div>
              <pre>{code}</pre>
            </Reveal>
          </Parallax>
          <Reveal className="integ-text" x={16} y={0} delay={0.1}>
            <dl className="facts">
              {t('integration.facts').map(([dt, dd]) => (
                <div key={dt}>
                  <dt>{dt}</dt>
                  <dd>{dd}</dd>
                </div>
              ))}
            </dl>
            <a href="/static/api-docs.html" className="lp-link">
              {t('integration.docs_link')}
              <Icon name="arrow-right" className="ic-sm" />
            </a>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

export default Integration;
