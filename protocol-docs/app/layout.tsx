import { Inter, Space_Grotesk, JetBrains_Mono } from 'next/font/google';
import type { Metadata } from 'next';
import { Provider } from '@/components/provider';
import './global.css';
import 'katex/dist/katex.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://docs.rld.fi'),
  title: 'RLD Protocol Documentation',
  description: 'Interest rate derivatives for on-chain finance',
};

const inter = Inter({
  subsets: ['latin'],
});

const spaceGrotesk = Space_Grotesk({
  subsets: ['latin'],
  variable: '--font-space',
  weight: ['300', '400', '500'],
});

const jbm = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jbm',
  weight: ['400', '500', '700'],
});

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html
      lang="en"
      className={`${inter.className} ${spaceGrotesk.variable} ${jbm.variable}`}
      suppressHydrationWarning
    >
      <body className="flex flex-col min-h-screen">
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
