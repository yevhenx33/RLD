import React, { useState, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import Header from './Header';
import axios from 'axios';

export default function Layout() {
    const [headerData, setHeaderData] = useState({
        latest: { block_number: 0 },
        isCapped: false,
        ratesLoaded: false
    });

    useEffect(() => {
        const fetchStatus = async () => {
            try {
                // Lightweight fetch for status only
                const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://rate-dashboard.onrender.com";
                const res = await axios.get(`${API_BASE}/rates?resolution=RAW&limit=1`);
                const data = res.data;
                
                if (data && data.length > 0) {
                    const latest = data[data.length - 1];
                    setHeaderData({
                        latest: latest,
                        isCapped: false, // In layout check we assume false/irrelevant for global header unless strictly needed
                        ratesLoaded: true
                    });
                }
            } catch (err) {
                console.error("Global Layout Status Fetch Error:", err);
                setHeaderData(prev => ({ ...prev, ratesLoaded: false }));
            }
        };

        fetchStatus();
        const interval = setInterval(fetchStatus, 15000); // Check every 15s
        return () => clearInterval(interval);
    }, []);

    return (
        <>
            <Header 
                latest={headerData.latest} 
                isCapped={headerData.isCapped} 
                ratesLoaded={headerData.ratesLoaded} 
            />
            <Outlet />
        </>
    );
}
