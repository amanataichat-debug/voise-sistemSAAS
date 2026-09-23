import React from 'react';

// Иконка из внешнего SVG-спрайта Lucide (/static/icons/ui.svg, символы `i-<name>`).
function Icon({ name, className = '' }) {
  return (
    <svg className={`ic ${className}`.trim()} aria-hidden="true">
      <use href={`/static/icons/ui.svg#i-${name}`} />
    </svg>
  );
}

export default Icon;
