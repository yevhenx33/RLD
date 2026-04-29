import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { gitConfig } from './shared';

const TwitterIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 4s-.7 2.1-2 3.4c1.6 10-9.4 17.3-18 11.6 2.2.1 4.4-.6 6-2C3 15.5.5 9.6 3 5c2.2 2.6 5.6 4.1 9 4-.9-4.2 4-6.6 7-3.8 1.1 0 3-1.2 3-1.2z" />
  </svg>
);

const TelegramIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
);

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <>
          <div
            style={{
              width: 9,
              height: 9,
              backgroundColor: '#ffffff',
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: '0.15em',
              textTransform: 'uppercase' as const,
              color: '#ffffff',
            }}
          >
            RLD
          </span>
        </>
      ),
      url: '/home',
    },
    themeSwitch: { enabled: false },
  };
}

export const DocsFooter = () => (
  <div className="flex flex-col space-y-3 p-4 border-t border-fd-border mt-auto">
    <a
      href={`https://github.com/${gitConfig.user}/${gitConfig.repo}`}
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-2 text-sm text-fd-muted-foreground hover:text-fd-foreground transition-colors"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
        <path d="M9 18c-4.51 2-5-2-7-2" />
      </svg>
      GitHub
    </a>
    <a
      href="https://x.com/rld_fi"
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-2 text-sm text-fd-muted-foreground hover:text-fd-foreground transition-colors"
    >
      <TwitterIcon />
      X/Twitter
    </a>
    <a
      href="https://t.me/+xGbwa0eKhrNkYzYy"
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-2 text-sm text-fd-muted-foreground hover:text-fd-foreground transition-colors"
    >
      <TelegramIcon />
      Telegram
    </a>
  </div>
);
