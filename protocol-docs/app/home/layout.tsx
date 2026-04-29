import { source } from '@/lib/source';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { baseOptions, DocsFooter } from '@/lib/layout.shared';

export default function Layout({ children }: LayoutProps<'/home'>) {
  return (
    <DocsLayout tree={source.getPageTree()} {...baseOptions()} sidebar={{ footer: <DocsFooter /> }}>
      {children}
    </DocsLayout>
  );
}
