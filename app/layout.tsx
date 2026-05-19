import '@/styles/globals.css';
import { ReactNode } from 'react';

export const metadata = {
  title: 'Boston Latin Dance',
  description: 'Find bachata, salsa & Latin dance events in Greater Boston',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full overflow-hidden w-full">
      <body className="h-full overflow-hidden w-full">{children}</body>
    </html>
  );
}
