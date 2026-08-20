import type { ReactNode } from 'react';
import Icon, { type IconName } from './Icons';

type Props = {
  icon: IconName;
  children: ReactNode;
  className?: string;
};

/** Date / place / cost row: fixed icon gutter so facts scan as a column. */
export default function MetaRow({ icon, children, className = '' }: Props) {
  return (
    <div className={`meta-row ${className}`.trim()}>
      <span className="meta-row-icon">
        <Icon name={icon} />
      </span>
      <span className="meta-row-text">{children}</span>
    </div>
  );
}
