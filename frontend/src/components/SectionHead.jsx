import React from 'react';
import { Reveal } from './Reveal';

// Общая шапка раздела: моно-индекс слева, заголовок и лид; огромная «призрачная»
// цифра индекса рисуется через .sh::before из data-index.
function SectionHead({ index, title, lead, children }) {
  return (
    <Reveal className="sh" y={16} data-index={index}>
      <span className="sh-index">{index}</span>
      <div className="sh-text">
        <h2>{title}</h2>
        {lead && <p className="lp-lead">{lead}</p>}
        {children}
      </div>
    </Reveal>
  );
}

export default SectionHead;
