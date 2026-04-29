import { getPageImage, source } from '@/lib/source';
import { notFound } from 'next/navigation';
import { ImageResponse } from 'next/og';
import { generate as DefaultImage } from 'fumadocs-ui/og';
import { appName } from '@/lib/shared';

export const revalidate = false;

export async function GET(_req: Request, { params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const page = source.getPage(slug.slice(0, -1));
  if (!page) notFound();

  const isParametricCds = page.slugs.join('/') === 'research/parametric-cds';

  return new ImageResponse(
    (
      <div
        style={{
          height: '100%',
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          backgroundColor: '#0a0a0a',
          padding: '80px',
          border: '1px solid #262626',
          fontFamily: 'monospace',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '30px' }}>
          <div
            style={{
              fontFamily: '"Space Grotesk", sans-serif',
              fontSize: '64px',
              fontWeight: 300,
              color: 'white',
              lineHeight: 1.08,
              letterSpacing: '-0.025em',
            }}
          >
            {page.data.title}
          </div>
          {page.data.description && (
            <div
              style={{
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: '32px',
                color: '#999999',
                lineHeight: 1.5,
                letterSpacing: '0.05em',
                maxWidth: '900px',
              }}
            >
              {page.data.description}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <div
            style={{
              width: 24,
              height: 24,
              backgroundColor: '#ffffff',
              flexShrink: 0,
            }}
          />
          <div
            style={{
              fontSize: '32px',
              fontWeight: 900,
              letterSpacing: '0.15em',
              textTransform: 'uppercase',
              color: 'white',
            }}
          >
            RLD
          </div>
        </div>
      </div>
    ),
    {
      width: 1200,
      height: 630,
    },
  );
}

export function generateStaticParams() {
  return source.getPages().map((page) => ({
    lang: page.locale,
    slug: getPageImage(page).segments,
  }));
}
