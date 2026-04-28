import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { gitConfig } from './shared';

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
    },
    githubUrl: `https://github.com/${gitConfig.user}/${gitConfig.repo}`,
    themeSwitch: { enabled: false },
  };
}
