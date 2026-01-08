import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import Header from './Header';
import { useWallet } from '../context/WalletContext';
import { FileText, ArrowUpRight, Calendar, Tag, ChevronRight } from 'lucide-react';
import { BLOG_POSTS } from '../data/posts';

export default function Research() {
    // Only used for Header compatibility
    const { account } = useWallet();
    const latest = { block_number: 0 }; // Placeholder

    return (
        <div className="min-h-screen bg-[#080808] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
            <Header latest={latest} isCapped={false} ratesLoaded={true} />

            <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 py-12">
                
                {/* PAGE HEADER */}
                <div className="mb-12 border-b border-white/10 pb-6 flex items-end justify-between">
                    <div>
                        <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2 font-bold">
                            Knowledge_Base
                        </div>
                        <h1 className="text-4xl font-light tracking-tight text-white">
                            RESEARCH <span className="text-gray-600">&</span> ANALYSIS
                        </h1>
                    </div>
                    <div className="text-right hidden md:block">
                        <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
                            Total_Articles
                        </div>
                        <div className="text-2xl font-mono text-white">
                            {BLOG_POSTS.length.toString().padStart(2, '0')}
                        </div>
                    </div>
                </div>

                {/* BLOG GRID */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {BLOG_POSTS.map((post) => (
                        <Link 
                            to={`/research/${post.id}`}
                            key={post.id}
                            className="group border border-white/10 bg-[#0a0a0a] hover:bg-white/5 transition-all duration-300 flex flex-col h-full cursor-pointer relative overflow-hidden"
                        >
                            {/* Hover Accent Line */}
                            <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-white/50 to-transparent scale-x-0 group-hover:scale-x-100 transition-transform duration-500" />

                            <div className="p-8 flex flex-col flex-1 h-full">
                                {/* Meta Header */}
                                <div className="flex justify-between items-start mb-6">
                                    <div className="flex flex-col gap-1">
                                        <span className="text-[10px] font-bold text-cyan-500 tracking-widest uppercase mb-1">
                                            {post.category}
                                        </span>
                                        <span className="text-[10px] text-gray-500 font-mono flex items-center gap-2 uppercase tracking-tight">
                                            <Calendar size={10} /> {post.date}
                                        </span>
                                    </div>
                                    <ArrowUpRight size={16} className="text-gray-600 group-hover:text-white transition-colors" />
                                </div>

                                {/* Content */}
                                <h3 className="text-xl text-white font-medium leading-tight mb-4 group-hover:underline decoration-1 underline-offset-4 decoration-white/30">
                                    {post.title}
                                </h3>
                                <p className="text-sm text-gray-400 leading-relaxed mb-8 flex-1">
                                    {post.summary}
                                </p>

                                {/* Footer */}
                                <div className="mt-auto pt-6 border-t border-white/5 flex items-center justify-between group-hover:border-white/20 transition-colors">
                                    <span className="text-[10px] text-gray-600 uppercase tracking-widest font-bold">
                                        {post.readTime}
                                    </span>
                                    <span className="text-xs text-white flex items-center gap-1 font-bold tracking-wider opacity-0 group-hover:opacity-100 transition-opacity transform translate-x-[-10px] group-hover:translate-x-0 duration-300">
                                        READ_ENTRY <ChevronRight size={12} />
                                    </span>
                                </div>
                            </div>
                        </Link>
                    ))}
                </div>
            </div>
        </div>
    );
}
