import React from 'react';
import { useParams, Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { ArrowLeft, Calendar, Tag, Clock, Share2, Copy } from 'lucide-react';
import Header from './Header';
import { BLOG_POSTS } from '../data/posts';
import { useWallet } from '../context/WalletContext';

export default function Article() {
  const { id } = useParams();
  const post = BLOG_POSTS.find(p => p.id === Number(id));
  const { account } = useWallet();
  const latest = { block_number: 0 }; // Placeholder

  if (!post) {
    return (
      <div className="min-h-screen bg-[#080808] text-white flex items-center justify-center font-mono">
        ARTICLE_NOT_FOUND
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#080808] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <Header latest={latest} isCapped={false} ratesLoaded={true} />

      <div className="max-w-[1000px] mx-auto w-full px-6 flex-1 py-12">
        {/* BACK BUTTON */}
        <Link 
          to="/research" 
          className="inline-flex items-center gap-2 text-xs text-gray-500 hover:text-white uppercase tracking-widest mb-12 transition-colors group"
        >
          <ArrowLeft size={14} className="group-hover:-translate-x-1 transition-transform" />
          Back_to_Research
        </Link>
        
        {/* ARTICLE HEADER */}
        <div className="mb-12 border-b border-white/10 pb-8">
          <div className="flex items-center gap-4 mb-6">
            <span className="bg-cyan-900/20 text-cyan-400 border border-cyan-500/30 px-3 py-1 text-[10px] uppercase tracking-widest font-bold">
              {post.category}
            </span>
            <span className="text-[10px] text-gray-500 flex items-center gap-2 uppercase tracking-widest">
              <Calendar size={12} /> {post.date}
            </span>
            <span className="text-[10px] text-gray-500 flex items-center gap-2 uppercase tracking-widest">
              <Clock size={12} /> {post.readTime}
            </span>
          </div>
          
          <h1 className="text-3xl md:text-5xl font-light text-white leading-tight tracking-tight mb-8">
            {post.title}
          </h1>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="w-8 h-8 bg-white/10 rounded-full flex items-center justify-center text-xs font-bold text-gray-400">
                RLD
              </div>
              <div className="text-xs text-gray-400">
                <div className="uppercase tracking-widest font-bold text-white">Research Team</div>
                <div>Official Core Contributor</div>
              </div>
            </div>
            
            <div className="flex items-center gap-3">
              <button className="p-2 border border-white/10 hover:border-white text-gray-500 hover:text-white transition-all rounded-none">
                <Share2 size={16} />
              </button>
              <button className="p-2 border border-white/10 hover:border-white text-gray-500 hover:text-white transition-all rounded-none">
                <Copy size={16} />
              </button>
            </div>
          </div>
        </div>

        {/* ARTICLE CONTENT */}
        <div className="prose prose-invert prose-lg max-w-none 
          prose-headings:font-light prose-headings:tracking-tight prose-headings:text-white
          prose-p:text-gray-300 prose-p:leading-relaxed prose-p:font-sans
          prose-code:font-mono prose-code:text-pink-400 prose-code:bg-white/5 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-none prose-code:before:content-none prose-code:after:content-none
          prose-pre:bg-[#0a0a0a] prose-pre:border prose-pre:border-white/10 prose-pre:rounded-none
          prose-strong:text-white prose-strong:font-bold
          prose-blockquote:border-l-2 prose-blockquote:border-cyan-500/50 prose-blockquote:bg-cyan-900/10 prose-blockquote:py-2 prose-blockquote:px-6 prose-blockquote:not-italic prose-blockquote:text-gray-300
          prose-li:text-gray-300
          prose-table:border-collapse prose-th:text-xs prose-th:uppercase prose-th:tracking-widest prose-th:text-gray-500 prose-th:border-b prose-th:border-white/10 prose-th:pb-4 prose-th:font-bold
          prose-td:py-4 prose-td:border-b prose-td:border-white/5 prose-td:font-mono prose-td:text-sm
        ">
          <ReactMarkdown 
            remarkPlugins={[remarkMath]} 
            rehypePlugins={[rehypeKatex]}
            components={{
              // Custom Link Renderer for internal consistency if needed
              a: ({node, ...props}) => <a {...props} className="text-cyan-400 hover:text-cyan-300 no-underline border-b border-cyan-500/30 transition-colors" />
            }}
          >
            {post.content}
          </ReactMarkdown>
        </div>

        {/* FOOTER NAV */}
        <div className="mt-20 pt-12 border-t border-white/10 flex justify-between items-center">
            <Link to="/research" className="text-xs uppercase tracking-widest text-gray-500 hover:text-white transition-colors">
                ← Back to Overview
            </Link>
             <Link to="#" className="text-xs uppercase tracking-widest text-gray-500 hover:text-white transition-colors">
                Next Article →
            </Link>
        </div>

      </div>
    </div>
  );
}
