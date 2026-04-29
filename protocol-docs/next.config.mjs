import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  output: 'standalone',
  images: {
    unoptimized: true,
  },
  reactStrictMode: true,
};

export default withMDX(config);
