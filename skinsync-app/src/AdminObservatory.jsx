import React, { useState, useEffect, useRef } from 'react';
import { 
  Database, Search, Filter, ShieldCheck, ChevronLeft, ChevronRight, 
  ExternalLink, Eye, X, Loader2, Store, BadgePercent, TrendingDown, 
  Check, AlertTriangle, Activity, Sparkles, CheckCircle2, ArrowRight
} from 'lucide-react';

const AdminObservatory = ({ onBackToApp }) => {
  // Overview stats
  const [overview, setOverview] = useState(null);
  const [loadingOverview, setLoadingOverview] = useState(true);

  // Products Table States
  const [products, setProducts] = useState([]);
  const [totalProducts, setTotalProducts] = useState(0);
  const [loadingProducts, setLoadingProducts] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedStore, setSelectedStore] = useState('');
  const [hasIngredientsFilter, setHasIngredientsFilter] = useState('');
  const [multiStoreFilter, setMultiStoreFilter] = useState('');
  const [gateFilter, setGateFilter] = useState('');
  const [page, setPage] = useState(0);
  const pageSize = 20;

  // Telemetry Modal States
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [telemetry, setTelemetry] = useState(null);
  const [loadingTelemetry, setLoadingTelemetry] = useState(false);
  const [activeTab, setActiveTab] = useState('scraped'); // 'scraped', 'ingredients', 'inci', 'pricing', 'dermo', 'similar'

  const searchDebounceRef = useRef(null);

  // Load Overview Data
  useEffect(() => {
    fetchOverview();
  }, []);

  const fetchOverview = async () => {
    try {
      setLoadingOverview(true);
      const res = await fetch('http://127.0.0.1:8000/admin/overview');
      if (res.ok) {
        const data = await res.json();
        setOverview(data);
      }
    } catch (err) {
      console.error('Failed to load admin overview:', err);
    } finally {
      setLoadingOverview(false);
    }
  };

  // Load Products with filters
  const fetchProducts = async (currentPage = page, query = searchQuery) => {
    try {
      setLoadingProducts(true);
      const params = new URLSearchParams();
      params.append('limit', pageSize.toString());
      params.append('offset', (currentPage * pageSize).toString());

      if (query.trim()) params.append('q', query.trim());
      if (selectedStore) params.append('store_id', selectedStore);
      if (hasIngredientsFilter === 'yes') params.append('has_ingredients', 'true');
      if (hasIngredientsFilter === 'no') params.append('has_ingredients', 'false');
      if (multiStoreFilter === 'multi') params.append('is_multi_store', 'true');
      if (multiStoreFilter === 'single') params.append('is_multi_store', 'false');
      if (gateFilter) params.append('gate_status', gateFilter);

      const res = await fetch(`http://127.0.0.1:8000/admin/products?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setProducts(data.products || []);
        setTotalProducts(data.total || 0);
      }
    } catch (err) {
      console.error('Failed to load products:', err);
    } finally {
      setLoadingProducts(false);
    }
  };

  // Re-fetch when filters or page change
  useEffect(() => {
    fetchProducts(page, searchQuery);
  }, [page, selectedStore, hasIngredientsFilter, multiStoreFilter, gateFilter]);

  // Handle Search Input with Debounce
  const handleSearchChange = (val) => {
    setSearchQuery(val);
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      setPage(0);
      fetchProducts(0, val);
    }, 350);
  };

  // Open Telemetry Forensics Modal
  const handleOpenTelemetry = async (productId) => {
    setSelectedProductId(productId);
    setActiveTab('scraped');
    setLoadingTelemetry(true);
    setTelemetry(null);
    try {
      const res = await fetch(`http://127.0.0.1:8000/admin/products/${productId}/telemetry`);
      if (res.ok) {
        const data = await res.json();
        setTelemetry(data);
      } else {
        alert('Could not fetch forensic telemetry for this product.');
      }
    } catch (err) {
      console.error('Error fetching product telemetry:', err);
    } finally {
      setLoadingTelemetry(false);
    }
  };

  const totalPages = Math.ceil(totalProducts / pageSize);

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 font-sans pb-20">
      {/* Top Navigation */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-40 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-tr from-purple-700 to-indigo-600 rounded-xl flex items-center justify-center text-white shadow-md">
              <ShieldCheck className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-black text-gray-900">Admin Observatory & Telemetry Portal</h1>
                <span className="bg-purple-100 text-purple-700 text-xs px-2.5 py-0.5 rounded-full font-bold">
                  v3.0 Production
                </span>
              </div>
              <p className="text-xs text-gray-500 font-medium">Deep Forensics, INCI Decision Logs & Multi-Store Telemetry</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={fetchOverview}
              className="text-xs text-gray-600 hover:text-purple-600 font-semibold px-3 py-2 rounded-lg border border-gray-200 bg-gray-50 hover:bg-gray-100 transition-all flex items-center gap-1.5 cursor-pointer"
            >
              <Activity className="w-3.5 h-3.5" />
              <span>Refresh Stats</span>
            </button>
            <button
              onClick={onBackToApp}
              className="bg-gray-900 hover:bg-purple-600 text-white font-bold text-xs px-4 py-2 rounded-xl transition-all flex items-center gap-1.5 cursor-pointer"
            >
              <ChevronLeft className="w-4 h-4" />
              <span>Back to SkinSync App</span>
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 pt-8 space-y-8">
        
        {/* 1. System Vitals & Ingestion Counters */}
        {overview && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">Catalog Listings</div>
              <div className="text-2xl font-black text-gray-900">
                {overview.catalog_vitals.total_store_listings.toLocaleString()}
              </div>
              <div className="text-[11px] text-gray-500 mt-1">Across 6 retailers</div>
            </div>

            <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">Canonical Entities</div>
              <div className="text-2xl font-black text-purple-700">
                {overview.catalog_vitals.canonical_entities.toLocaleString()}
              </div>
              <div className="text-[11px] text-gray-500 mt-1">Unified product clusters</div>
            </div>

            <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">Multi-Store Deals</div>
              <div className="text-2xl font-black text-emerald-600">
                {overview.catalog_vitals.multi_store_clusters.toLocaleString()}
              </div>
              <div className="text-[11px] text-emerald-700 mt-1 font-semibold">Active price arbitrage</div>
            </div>

            <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">Full Ingredients</div>
              <div className="text-2xl font-black text-blue-600">
                {overview.catalog_vitals.products_with_ingredients.toLocaleString()}
              </div>
              <div className="text-[11px] text-gray-500 mt-1">
                {overview.catalog_vitals.coverage_percent}% formulation coverage
              </div>
            </div>

            <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">INCI Reference Links</div>
              <div className="text-2xl font-black text-indigo-600">
                {overview.catalog_vitals.inci_matched_products.toLocaleString()}
              </div>
              <div className="text-[11px] text-gray-500 mt-1">SkinSafe & curated DB</div>
            </div>

            <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">AI Verified Matches</div>
              <div className="text-2xl font-black text-pink-600">
                {overview.catalog_vitals.ai_verified_products.toLocaleString()}
              </div>
              <div className="text-[11px] text-gray-500 mt-1">Cross-Encoder & Gemini</div>
            </div>
          </div>
        )}

        {/* 2. Retail Store Health Breakdown */}
        {overview && (
          <div className="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm">
            <h2 className="text-sm font-bold text-gray-700 uppercase tracking-wider mb-4 flex items-center gap-2">
              <Store className="w-4 h-4 text-purple-600" />
              <span>Scraped Retail Sources Breakdown</span>
            </h2>

            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              {overview.stores.map((s) => (
                <div 
                  key={s.id}
                  onClick={() => { setSelectedStore(selectedStore === s.id ? '' : s.id); setPage(0); }}
                  className={`p-3.5 rounded-xl border text-left transition-all cursor-pointer ${
                    selectedStore === s.id 
                      ? 'border-purple-600 bg-purple-50/50 shadow-sm' 
                      : 'border-gray-200 bg-gray-50/50 hover:bg-white hover:border-purple-300'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-extrabold text-xs text-gray-900 truncate">{s.name}</span>
                    <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
                  </div>
                  <div className="text-lg font-black text-purple-700">{s.total_products.toLocaleString()}</div>
                  <div className="text-[10px] text-gray-500 flex items-center justify-between mt-1">
                    <span>{s.with_ingredients} w/ INCI</span>
                    <span className="font-semibold">{s.avg_price} EGP</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 3. Catalog Explorer with Multi-Filters */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
          <div className="p-6 border-b border-gray-200 space-y-4">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div>
                <h2 className="text-lg font-black text-gray-900 flex items-center gap-2">
                  <Database className="w-5 h-5 text-purple-600" />
                  <span>Product Catalog & Forensic Explorer</span>
                </h2>
                <p className="text-xs text-gray-500">
                  Inspect raw scraped HTML, formulation tokens, INCI gates, and multi-store offers
                </p>
              </div>

              <div className="text-xs font-semibold text-gray-500">
                Found <span className="font-bold text-gray-900">{totalProducts.toLocaleString()}</span> products
              </div>
            </div>

            {/* Filter Bar */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 pt-2">
              {/* Search Query */}
              <div className="relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-3" />
                <input
                  type="text"
                  placeholder="Search title, brand, handle..."
                  value={searchQuery}
                  onChange={(e) => handleSearchChange(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 focus:bg-white transition-all"
                />
              </div>

              {/* Store Filter */}
              <select
                value={selectedStore}
                onChange={(e) => { setSelectedStore(e.target.value); setPage(0); }}
                className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500"
              >
                <option value="">All Stores (6 Retailers)</option>
                <option value="sourcebeauty">Source Beauty</option>
                <option value="feel22">Feel22 Egypt</option>
                <option value="bloompharmacy">Bloom Pharmacy</option>
                <option value="drdawood">Dr Dawood Pharmacy</option>
                <option value="bashrety">Bashrety</option>
                <option value="rawafrican">Raw African</option>
              </select>

              {/* Ingredients Presence */}
              <select
                value={hasIngredientsFilter}
                onChange={(e) => { setHasIngredientsFilter(e.target.value); setPage(0); }}
                className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500"
              >
                <option value="">Ingredients (All)</option>
                <option value="yes">Has Full Ingredients List</option>
                <option value="no">Missing Ingredients List</option>
              </select>

              {/* Multi-Store Arbitrage */}
              <select
                value={multiStoreFilter}
                onChange={(e) => { setMultiStoreFilter(e.target.value); setPage(0); }}
                className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500"
              >
                <option value="">Store Presence (All)</option>
                <option value="multi">Multi-Store Product (Arbitrage)</option>
                <option value="single">Single Store Only</option>
              </select>

              {/* Gate Status */}
              <select
                value={gateFilter}
                onChange={(e) => { setGateFilter(e.target.value); setPage(0); }}
                className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500"
              >
                <option value="">INCI Decision Status (All)</option>
                <option value="gemini_approved">Gemini Approved</option>
                <option value="auto_approved">Auto Approved (Cross-Encoder)</option>
                <option value="pending_gemini">Pending Gemini Verification</option>
                <option value="rejected_safeguard">Rejected by Cosmetic Safeguard</option>
              </select>
            </div>
          </div>

          {/* Table Container */}
          <div className="overflow-x-auto">
            {loadingProducts ? (
              <div className="py-20 text-center flex flex-col items-center justify-center gap-3">
                <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
                <span className="text-xs text-gray-500 font-medium">Scanning catalog records...</span>
              </div>
            ) : products.length === 0 ? (
              <div className="py-20 text-center text-gray-500 text-xs font-semibold">
                No products found matching the selected filters.
              </div>
            ) : (
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-gray-50/80 border-b border-gray-200 text-gray-500 uppercase font-extrabold text-[10px] tracking-wider">
                    <th className="py-3 px-4">Product</th>
                    <th className="py-3 px-4">Store</th>
                    <th className="py-3 px-4">Price</th>
                    <th className="py-3 px-4">Ingredients Source</th>
                    <th className="py-3 px-4">Cluster / Stores</th>
                    <th className="py-3 px-4">INCI Status</th>
                    <th className="py-3 px-4 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {products.map((p) => (
                    <tr 
                      key={p.id} 
                      className="hover:bg-purple-50/30 transition-colors group cursor-pointer"
                      onClick={() => handleOpenTelemetry(p.id)}
                    >
                      {/* Product Column */}
                      <td className="py-3.5 px-4 max-w-sm">
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 rounded-lg bg-gray-50 border border-gray-200 flex items-center justify-center shrink-0 overflow-hidden">
                            {p.image_url ? (
                              <img src={p.image_url} alt="" className="max-h-full max-w-full object-contain" onError={(e) => { e.target.style.display = 'none'; }} />
                            ) : (
                              <span className="text-sm">🧴</span>
                            )}
                          </div>
                          <div className="truncate">
                            <div className="text-[10px] font-bold text-purple-600 uppercase">{p.brand}</div>
                            <div className="font-bold text-gray-900 truncate" title={p.title}>
                              {p.title}
                            </div>
                            <div className="text-[10px] text-gray-400 truncate">ID: #{p.id} • {p.category}</div>
                          </div>
                        </div>
                      </td>

                      {/* Store Column */}
                      <td className="py-3.5 px-4">
                        <span className="bg-gray-100 text-gray-700 font-semibold px-2 py-0.5 rounded text-[11px] whitespace-nowrap">
                          {p.store_name || p.store_id}
                        </span>
                      </td>

                      {/* Price Column */}
                      <td className="py-3.5 px-4 font-black text-gray-900 whitespace-nowrap">
                        {p.price > 0 ? `${p.price} EGP` : <span className="text-gray-400 font-normal">N/A</span>}
                      </td>

                      {/* Ingredients Column */}
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        {p.has_ingredients ? (
                          <span className="bg-emerald-100 text-emerald-800 font-bold px-2 py-0.5 rounded-full text-[10px] flex items-center gap-1 w-fit">
                            <Check className="w-3 h-3" /> {p.ingredient_source || 'Available'}
                          </span>
                        ) : (
                          <span className="bg-gray-100 text-gray-400 font-medium px-2 py-0.5 rounded text-[10px]">
                            Missing
                          </span>
                        )}
                      </td>

                      {/* Cluster / Stores Column */}
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        {p.store_count > 1 ? (
                          <span className="bg-pink-100 text-pink-700 font-bold px-2 py-0.5 rounded-full text-[10px] flex items-center gap-1 w-fit">
                            <Store className="w-3 h-3" /> {p.store_count} Stores ({p.min_price} - {p.max_price})
                          </span>
                        ) : (
                          <span className="text-gray-400 text-[11px]">1 Store</span>
                        )}
                      </td>

                      {/* INCI Status Column */}
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        {p.gate4_gemini_status === 'gemini_approved' ? (
                          <span className="bg-emerald-100 text-emerald-800 font-bold px-2 py-0.5 rounded-full text-[10px]">
                            Gemini Approved
                          </span>
                        ) : p.gate4_cross_status === 'auto_approved' ? (
                          <span className="bg-purple-100 text-purple-800 font-bold px-2 py-0.5 rounded-full text-[10px]">
                            Auto Approved
                          </span>
                        ) : p.gate4_gemini_status === 'pending_gemini' ? (
                          <span className="bg-amber-100 text-amber-800 font-bold px-2 py-0.5 rounded-full text-[10px]">
                            Pending Gemini
                          </span>
                        ) : p.gate2_safeguard_status && p.gate2_safeguard_status.startsWith('rejected') ? (
                          <span className="bg-red-100 text-red-700 font-bold px-2 py-0.5 rounded-full text-[10px]">
                            Safeguard Rejected
                          </span>
                        ) : p.inci_matched_id ? (
                          <span className="bg-blue-100 text-blue-700 font-bold px-2 py-0.5 rounded-full text-[10px]">
                            INCI Linked ({p.inci_hybrid_score ? Math.round(p.inci_hybrid_score * 100) + '%' : 'Linked'})
                          </span>
                        ) : (
                          <span className="text-gray-400 text-[10px]">Unmatched</span>
                        )}
                      </td>

                      {/* Action Column */}
                      <td className="py-3.5 px-4 text-right whitespace-nowrap">
                        <button
                          onClick={(e) => { e.stopPropagation(); handleOpenTelemetry(p.id); }}
                          className="px-3 py-1.5 bg-purple-100 hover:bg-purple-200 text-purple-700 font-bold rounded-lg text-xs flex items-center gap-1 ml-auto cursor-pointer"
                        >
                          <Eye className="w-3.5 h-3.5" />
                          <span>Inspect</span>
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination Footer */}
          <div className="p-4 border-t border-gray-200 bg-gray-50 flex items-center justify-between text-xs text-gray-500">
            <div>
              Showing <span className="font-bold text-gray-900">{page * pageSize + 1}</span> to{' '}
              <span className="font-bold text-gray-900">{Math.min(totalProducts, (page + 1) * pageSize)}</span> of{' '}
              <span className="font-bold text-gray-900">{totalProducts.toLocaleString()}</span> listings
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className="p-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-100 disabled:opacity-40 cursor-pointer"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="font-semibold px-2">Page {page + 1} of {Math.max(1, totalPages)}</span>
              <button
                onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                disabled={page >= totalPages - 1}
                className="p-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-100 disabled:opacity-40 cursor-pointer"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

      </div>

      {/* ========================================================================= */}
      {/* 4. IN-DEPTH PRODUCT TELEMETRY FORENSICS MODAL                             */}
      {/* ========================================================================= */}
      {selectedProductId && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-3xl max-w-4xl w-full max-h-[92vh] shadow-2xl flex flex-col overflow-hidden border border-gray-200">
            
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-purple-800 to-indigo-900 p-6 text-white shrink-0 relative">
              <button
                onClick={() => setSelectedProductId(null)}
                className="absolute top-5 right-5 text-white/80 hover:text-white bg-white/10 hover:bg-white/20 p-2 rounded-full transition-all cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>

              <div className="flex items-center gap-2 mb-2">
                <span className="bg-purple-500/30 text-purple-200 text-[10px] uppercase font-black px-2.5 py-0.5 rounded-full border border-purple-400/30">
                  Forensic Audit Trail
                </span>
                {telemetry && (
                  <span className="bg-white/10 text-white text-[10px] font-bold px-2 py-0.5 rounded">
                    Store: {telemetry.product.store_name}
                  </span>
                )}
              </div>

              <h3 className="text-xl font-black leading-tight">
                {telemetry ? telemetry.product.title : 'Loading Product Details...'}
              </h3>
              {telemetry && (
                <p className="text-purple-200 text-xs mt-1">
                  Brand: {telemetry.product.brand} • Category: {telemetry.product.category} • Price: {telemetry.product.price} EGP
                </p>
              )}
            </div>

            {/* Navigation Tabs */}
            <div className="bg-gray-100 px-6 border-b border-gray-200 flex items-center gap-1 overflow-x-auto shrink-0 text-xs font-bold">
              <button
                onClick={() => setActiveTab('scraped')}
                className={`py-3 px-4 border-b-2 transition-all cursor-pointer ${activeTab === 'scraped' ? 'border-purple-600 text-purple-700 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                📋 Scraped Source Info
              </button>
              <button
                onClick={() => setActiveTab('ingredients')}
                className={`py-3 px-4 border-b-2 transition-all cursor-pointer ${activeTab === 'ingredients' ? 'border-purple-600 text-purple-700 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                🧬 Ingredients & Actives
              </button>
              <button
                onClick={() => setActiveTab('inci')}
                className={`py-3 px-4 border-b-2 transition-all cursor-pointer ${activeTab === 'inci' ? 'border-purple-600 text-purple-700 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                🎯 INCI Matching & Gates
              </button>
              <button
                onClick={() => setActiveTab('pricing')}
                className={`py-3 px-4 border-b-2 transition-all cursor-pointer ${activeTab === 'pricing' ? 'border-purple-600 text-purple-700 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                🏪 Multi-Store Pricing
              </button>
              <button
                onClick={() => setActiveTab('dermo')}
                className={`py-3 px-4 border-b-2 transition-all cursor-pointer ${activeTab === 'dermo' ? 'border-purple-600 text-purple-700 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                🩺 Clinical Dermo Safety
              </button>
              <button
                onClick={() => setActiveTab('similar')}
                className={`py-3 px-4 border-b-2 transition-all cursor-pointer ${activeTab === 'similar' ? 'border-purple-600 text-purple-700 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                🔮 Similar Alternatives
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6 overflow-y-auto flex-1 text-xs">
              {loadingTelemetry ? (
                <div className="py-24 text-center flex flex-col items-center justify-center gap-3">
                  <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
                  <span className="text-gray-500 font-semibold">Extracting forensic telemetry from database...</span>
                </div>
              ) : !telemetry ? (
                <div className="py-20 text-center text-gray-400">Failed to load telemetry.</div>
              ) : (
                <>
                  {/* TAB 1: SCRAPED SOURCE INFO */}
                  {activeTab === 'scraped' && (
                    <div className="space-y-6">
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 bg-gray-50 p-4 rounded-2xl border border-gray-200">
                        <div>
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Store Platform ID</div>
                          <div className="font-extrabold text-gray-900">{telemetry.product.store_product_id || 'N/A'}</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Handle (Slug)</div>
                          <div className="font-extrabold text-gray-900 truncate" title={telemetry.product.handle}>
                            {telemetry.product.handle || 'N/A'}
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Compare At Price</div>
                          <div className="font-extrabold text-gray-900">{telemetry.product.compare_at_price || 0} EGP</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Inventory Status</div>
                          <div className="font-extrabold text-emerald-600">
                            {telemetry.product.is_available ? 'In Stock' : 'Out of Stock'}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center justify-between p-4 bg-purple-50 rounded-xl border border-purple-100">
                        <div>
                          <div className="font-bold text-gray-900">Direct Retail Listing Link</div>
                          <div className="text-gray-500 truncate max-w-md">{telemetry.product.product_url}</div>
                        </div>
                        <a 
                          href={telemetry.product.product_url} 
                          target="_blank" 
                          rel="noreferrer"
                          className="px-4 py-2 bg-purple-600 text-white rounded-xl font-bold flex items-center gap-1 hover:bg-purple-700 transition"
                        >
                          Visit Store <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      </div>

                      {/* Raw HTML Description */}
                      <div>
                        <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                          Raw Scraped Retailer HTML Description
                        </div>
                        {telemetry.product.description_html ? (
                          <div className="p-4 bg-gray-50 rounded-xl border border-gray-200 max-h-64 overflow-y-auto space-y-2">
                            <div 
                              className="prose prose-xs max-w-none text-gray-700"
                              dangerouslySetInnerHTML={{ __html: telemetry.product.description_html }} 
                            />
                          </div>
                        ) : (
                          <div className="p-6 bg-gray-50 text-center text-gray-400 rounded-xl border">
                            No HTML description provided by retailer during crawl.
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* TAB 2: INGREDIENTS & ACTIVES FORENSICS */}
                  {activeTab === 'ingredients' && (
                    <div className="space-y-6">
                      <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl border border-gray-200">
                        <div>
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Ingestion Provenance</div>
                          <div className="text-sm font-extrabold text-purple-700 capitalize">
                            {telemetry.ingredients_forensics.ingredient_source}
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Normalized Tokens</div>
                          <div className="text-sm font-extrabold text-gray-900">
                            {telemetry.ingredients_forensics.parsed_tokens_count} items
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Identified Actives</div>
                          <div className="text-sm font-extrabold text-gray-900">
                            {telemetry.ingredients_forensics.actives.length} actives
                          </div>
                        </div>
                      </div>

                      {/* Detected Actives */}
                      <div>
                        <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                          Clinical Key Active Compounds
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {telemetry.ingredients_forensics.actives.length > 0 ? (
                            telemetry.ingredients_forensics.actives.map((act, i) => (
                              <span key={i} className="px-3 py-1 bg-purple-100 text-purple-800 font-extrabold rounded-lg text-xs">
                                ✨ {act}
                              </span>
                            ))
                          ) : (
                            <span className="text-gray-400 italic">No specific dermatological active compounds detected.</span>
                          )}
                        </div>
                      </div>

                      {/* Parsed Tokens */}
                      <div>
                        <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                          Parsed & Normalized Ingredient Order (FDA Concentration Weighting)
                        </div>
                        {telemetry.ingredients_forensics.ingredients_parsed.length > 0 ? (
                          <div className="p-4 bg-gray-50 rounded-xl border border-gray-200 max-h-60 overflow-y-auto">
                            <ol className="list-decimal list-inside space-y-1 text-gray-700 font-medium">
                              {telemetry.ingredients_forensics.ingredients_parsed.map((token, i) => (
                                <li key={i} className="pl-1">
                                  <span className="text-gray-900 font-semibold">{token}</span>
                                  {i < 5 && <span className="text-[10px] text-amber-600 font-bold ml-2">Zone 1 (&gt;1%)</span>}
                                </li>
                              ))}
                            </ol>
                          </div>
                        ) : (
                          <div className="p-6 bg-gray-50 text-center text-gray-400 rounded-xl border">
                            No ingredient tokens parsed.
                          </div>
                        )}
                      </div>

                      {/* Raw Scraped Ingredients */}
                      <div>
                        <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                          Raw Scraped Text (Pre-Normalization)
                        </div>
                        <div className="p-3 bg-gray-100 rounded-lg text-[11px] font-mono text-gray-800 break-words max-h-28 overflow-y-auto">
                          {telemetry.ingredients_forensics.ingredients_raw || 'No raw ingredients text recorded.'}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* TAB 3: INCI MATCHING & 4-GATE TELEMETRY */}
                  {activeTab === 'inci' && (
                    <div className="space-y-6">
                      {/* Gate Status Grid */}
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                        <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Gate 1: Data Quality</div>
                          <div className="font-bold text-gray-900 mt-1">
                            {telemetry.inci_matching_telemetry.gates.gate1_quality_status || 'N/A'}
                          </div>
                        </div>
                        <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Gate 2: Safeguard Rules</div>
                          <div className="font-bold text-gray-900 mt-1">
                            {telemetry.inci_matching_telemetry.gates.gate2_safeguard_status || 'N/A'}
                          </div>
                        </div>
                        <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Gate 3: Active Overlap</div>
                          <div className="font-bold text-gray-900 mt-1">
                            {telemetry.inci_matching_telemetry.gates.gate3_active_status || 'N/A'}
                          </div>
                        </div>
                        <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Gate 4: Cross-Encoder</div>
                          <div className="font-bold text-purple-700 mt-1">
                            {telemetry.inci_matching_telemetry.gates.gate4_cross_status || 'N/A'}
                          </div>
                        </div>
                        <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Gate 4: Jev 1.13</div>
                          <div className="font-bold text-gray-900 mt-1">
                            {telemetry.inci_matching_telemetry.gates.gate4_jev_status || 'N/A'}
                          </div>
                        </div>
                        <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                          <div className="text-[10px] text-gray-400 font-bold uppercase">Gate 4: Gemini Batch</div>
                          <div className="font-bold text-pink-600 mt-1">
                            {telemetry.inci_matching_telemetry.gates.gate4_gemini_status || 'N/A'}
                          </div>
                        </div>
                      </div>

                      {/* Scores Breakdown */}
                      <div>
                        <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                          Neural Retrieval & Token Alignment Scores
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                          <div className="p-3 bg-white rounded-xl border border-gray-200 text-center">
                            <div className="text-[10px] text-gray-400 font-semibold">Dense Vector (BGE)</div>
                            <div className="text-lg font-black text-blue-600">
                              {telemetry.inci_matching_telemetry.scores.vector_similarity ? Math.round(telemetry.inci_matching_telemetry.scores.vector_similarity * 100) + '%' : 'N/A'}
                            </div>
                          </div>
                          <div className="p-3 bg-white rounded-xl border border-gray-200 text-center">
                            <div className="text-[10px] text-gray-400 font-semibold">Token Ratio (RapidFuzz)</div>
                            <div className="text-lg font-black text-indigo-600">
                              {telemetry.inci_matching_telemetry.scores.token_similarity ? Math.round(telemetry.inci_matching_telemetry.scores.token_similarity * 100) + '%' : 'N/A'}
                            </div>
                          </div>
                          <div className="p-3 bg-white rounded-xl border border-gray-200 text-center">
                            <div className="text-[10px] text-gray-400 font-semibold">Calibrated Hybrid</div>
                            <div className="text-lg font-black text-purple-700">
                              {telemetry.inci_matching_telemetry.scores.hybrid_score ? Math.round(telemetry.inci_matching_telemetry.scores.hybrid_score * 100) + '%' : 'N/A'}
                            </div>
                          </div>
                          <div className="p-3 bg-white rounded-xl border border-gray-200 text-center">
                            <div className="text-[10px] text-gray-400 font-semibold">Cross-Encoder (MiniLM)</div>
                            <div className="text-lg font-black text-emerald-600">
                              {telemetry.inci_matching_telemetry.scores.cross_encoder_score ? Math.round(telemetry.inci_matching_telemetry.scores.cross_encoder_score * 100) + '%' : 'N/A'}
                            </div>
                          </div>
                        </div>
                      </div>

                      {/* Matched Reference Details */}
                      {telemetry.inci_matching_telemetry.reference_knowledge_base && (
                        <div className="p-4 bg-purple-50/60 rounded-xl border border-purple-200">
                          <div className="text-[10px] font-bold uppercase text-purple-700 mb-1">
                            Matched INCI Reference Record #{telemetry.inci_matching_telemetry.reference_knowledge_base.id}
                          </div>
                          <div className="font-extrabold text-sm text-gray-900">
                            {telemetry.inci_matching_telemetry.reference_knowledge_base.product_name}
                          </div>
                          <p className="text-gray-500 text-xs mt-1">
                            Brand: {telemetry.inci_matching_telemetry.reference_knowledge_base.brand} • Source: {telemetry.inci_matching_telemetry.reference_knowledge_base.source}
                          </p>
                          <div className="mt-3 text-[11px] text-gray-700 font-mono bg-white p-2.5 rounded-lg border border-purple-100 max-h-24 overflow-y-auto">
                            {telemetry.inci_matching_telemetry.reference_knowledge_base.ingredients_raw}
                          </div>
                        </div>
                      )}

                      {/* Safeguard & Decision Notes */}
                      {telemetry.inci_matching_telemetry.safeguard_log && (
                        <div>
                          <div className="font-bold text-gray-700 mb-1 uppercase text-[10px] tracking-wider">
                            Safeguard Decision Notes & Bypassed Candidate Log
                          </div>
                          <pre className="p-3 bg-gray-100 rounded-lg text-[11px] font-mono text-gray-700 whitespace-pre-wrap">
                            {telemetry.inci_matching_telemetry.safeguard_log}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}

                  {/* TAB 4: MULTI-STORE PRICING & LINKS */}
                  {activeTab === 'pricing' && (
                    <div className="space-y-6">
                      {telemetry.cross_store_linking.canonical_entity ? (
                        <>
                          <div className="p-4 bg-emerald-50 rounded-xl border border-emerald-200 flex flex-col sm:flex-row items-center justify-between gap-4">
                            <div>
                              <div className="text-[10px] uppercase font-bold text-emerald-700">Canonical Entity Resolution</div>
                              <h4 className="font-extrabold text-sm text-gray-900">
                                {telemetry.cross_store_linking.canonical_entity.canonical_title}
                              </h4>
                              <p className="text-xs text-gray-500">
                                Normalized Key: <code className="bg-white px-1 rounded border">{telemetry.cross_store_linking.canonical_entity.normalized_key}</code>
                              </p>
                            </div>

                            <div className="flex items-center gap-3 shrink-0">
                              <div className="text-right">
                                <span className="bg-emerald-600 text-white text-xs font-black px-3 py-1 rounded-full">
                                  Save {telemetry.cross_store_linking.canonical_entity.price_difference_egp} EGP ({telemetry.cross_store_linking.canonical_entity.savings_percent}%)
                                </span>
                                <div className="text-[10px] text-gray-500 mt-1">
                                  Range: {telemetry.cross_store_linking.canonical_entity.min_price} – {telemetry.cross_store_linking.canonical_entity.max_price} EGP
                                </div>
                              </div>
                            </div>
                          </div>

                          <div>
                            <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                              All Retailer Offers in Egypt ({telemetry.cross_store_linking.total_store_offers} Stores)
                            </div>
                            <div className="space-y-2.5">
                              {telemetry.cross_store_linking.sibling_offers.map((offer, idx) => (
                                <div
                                  key={idx}
                                  className={`p-3.5 rounded-xl border flex items-center justify-between transition-all ${
                                    offer.is_current_product
                                      ? 'border-purple-500 bg-purple-50/50'
                                      : offer.is_best_deal
                                      ? 'border-emerald-300 bg-emerald-50/40'
                                      : 'border-gray-200 bg-white'
                                  }`}
                                >
                                  <div>
                                    <div className="flex items-center gap-2">
                                      <span className="font-bold text-gray-900">{offer.store_name}</span>
                                      {offer.is_best_deal && (
                                        <span className="bg-emerald-600 text-white text-[9px] uppercase font-black px-2 py-0.5 rounded-full">
                                          Best Deal
                                        </span>
                                      )}
                                      {offer.is_current_product && (
                                        <span className="bg-purple-600 text-white text-[9px] uppercase font-bold px-2 py-0.5 rounded-full">
                                          Current Listing
                                        </span>
                                      )}
                                      {!offer.is_available && (
                                        <span className="bg-gray-200 text-gray-600 text-[9px] uppercase font-bold px-2 py-0.5 rounded-full">
                                          Out of Stock
                                        </span>
                                      )}
                                    </div>
                                    <p className="text-[11px] text-gray-500 truncate max-w-sm mt-0.5">{offer.store_title}</p>
                                  </div>

                                  <div className="flex items-center gap-4">
                                    <div className="text-right">
                                      <div className="font-black text-sm text-gray-900">{offer.price} EGP</div>
                                      <div className="text-[10px] text-gray-400 capitalize">{offer.ingredient_source || 'store'}</div>
                                    </div>
                                    <a
                                      href={offer.product_url}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="px-3 py-1.5 bg-gray-900 hover:bg-purple-600 text-white font-bold rounded-lg text-xs flex items-center gap-1 transition"
                                    >
                                      Store Link <ExternalLink className="w-3 h-3" />
                                    </a>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="p-8 text-center text-gray-400 bg-gray-50 rounded-2xl border">
                          This product is currently only sold at this single retailer.
                        </div>
                      )}
                    </div>
                  )}

                  {/* TAB 5: CLINICAL DERMO SAFETY */}
                  {activeTab === 'dermo' && (
                    <div className="space-y-6">
                      {telemetry.clinical_dermatology ? (
                        <>
                          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                            <div className="p-4 bg-white rounded-xl border border-gray-200 text-center">
                              <div className="text-[10px] text-gray-400 font-bold uppercase">Acne Comedogenicity Risk</div>
                              <div className="text-2xl font-black text-amber-600 mt-1">
                                {telemetry.clinical_dermatology.acne_risk_score} / 10
                              </div>
                            </div>
                            <div className="p-4 bg-white rounded-xl border border-gray-200 text-center">
                              <div className="text-[10px] text-gray-400 font-bold uppercase">Irritancy Risk Score</div>
                              <div className="text-2xl font-black text-rose-600 mt-1">
                                {telemetry.clinical_dermatology.irritancy_risk_score} / 10
                              </div>
                            </div>
                            <div className="p-4 bg-white rounded-xl border border-gray-200 text-center">
                              <div className="text-[10px] text-gray-400 font-bold uppercase">Tolerance Status</div>
                              <div className="text-2xl font-black text-emerald-600 mt-1">
                                {telemetry.clinical_dermatology.is_suitable_for_skin_type ? 'Tolerated' : 'Caution'}
                              </div>
                            </div>
                          </div>

                          {/* Flagged Ingredients */}
                          <div>
                            <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                              Comedogenic & Irritating Ingredients Flagged
                            </div>
                            <div className="space-y-2">
                              {telemetry.clinical_dermatology.comedogenic_ingredients.length > 0 && (
                                <div className="p-3 bg-amber-50 rounded-xl border border-amber-200">
                                  <span className="font-bold text-amber-800">Comedogenic: </span>
                                  <span className="text-amber-900">{telemetry.clinical_dermatology.comedogenic_ingredients.join(', ')}</span>
                                </div>
                              )}
                              {telemetry.clinical_dermatology.irritating_ingredients.length > 0 && (
                                <div className="p-3 bg-rose-50 rounded-xl border border-rose-200">
                                  <span className="font-bold text-rose-800">Irritants: </span>
                                  <span className="text-rose-900">{telemetry.clinical_dermatology.irritating_ingredients.join(', ')}</span>
                                </div>
                              )}
                              {telemetry.clinical_dermatology.comedogenic_ingredients.length === 0 && telemetry.clinical_dermatology.irritating_ingredients.length === 0 && (
                                <div className="p-4 bg-emerald-50 text-emerald-800 rounded-xl border border-emerald-200 font-semibold">
                                  ✓ Clean formulation: No severe clinical irritants or high pore-clogging agents flagged.
                                </div>
                              )}
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="p-8 text-center text-gray-400 bg-gray-50 rounded-2xl border">
                          No ingredients available to perform clinical dermatological evaluation.
                        </div>
                      )}
                    </div>
                  )}

                  {/* TAB 6: TOP SIMILAR FORMULATION MATCHES */}
                  {activeTab === 'similar' && (
                    <div className="space-y-4">
                      <div className="font-bold text-gray-700 uppercase text-[10px] tracking-wider">
                        Top Catalog Dupes & Alternatives Breakdown
                      </div>

                      {telemetry.similar_formulation_matches.length === 0 ? (
                        <div className="p-8 text-center text-gray-400 bg-gray-50 rounded-2xl border">
                          No formulation alternatives matched for this product.
                        </div>
                      ) : (
                        telemetry.similar_formulation_matches.map((alt, idx) => (
                          <div 
                            key={idx}
                            className="p-4 rounded-2xl border border-gray-200 bg-white hover:border-purple-300 transition-all space-y-3"
                          >
                            <div className="flex items-start justify-between gap-4">
                              <div className="flex items-center gap-3">
                                <div className="w-12 h-12 rounded-xl bg-gray-50 border border-gray-200 flex items-center justify-center shrink-0 overflow-hidden">
                                  {alt.image_url ? (
                                    <img src={alt.image_url} alt="" className="max-h-full max-w-full object-contain" onError={(e) => { e.target.style.display = 'none'; }} />
                                  ) : (
                                    <span className="text-lg">🧴</span>
                                  )}
                                </div>
                                <div>
                                  <div className="text-[10px] font-bold text-purple-600 uppercase">{alt.brand}</div>
                                  <h4 className="font-bold text-sm text-gray-900 leading-snug">{alt.title}</h4>
                                  <div className="text-[11px] text-gray-500 mt-0.5">{alt.price ? `${alt.price} EGP` : 'Price N/A'}</div>
                                </div>
                              </div>

                              <div className="text-right shrink-0">
                                <span className="bg-gradient-to-r from-purple-600 to-pink-600 text-white font-black text-xs px-3 py-1 rounded-full shadow-sm">
                                  {Math.round(alt.match_score)}% Match
                                </span>
                                {alt.cross_encoder_score && (
                                  <div className="text-[10px] text-gray-500 mt-1 font-semibold">
                                    Cross-Encoder: {Math.round(alt.cross_encoder_score * 100)}%
                                  </div>
                                )}
                              </div>
                            </div>

                            {/* Why it was matched */}
                            <div className="p-3 bg-purple-50/60 rounded-xl border border-purple-100 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-[11px]">
                              <div>
                                <span className="font-bold text-purple-900">Shared Actives: </span>
                                <span className="text-purple-700">
                                  {alt.shared_actives.length > 0 ? alt.shared_actives.join(', ') : 'Base formulation similarity'}
                                </span>
                              </div>
                              {alt.formula_similarity_percent !== null && (
                                <div className="font-bold text-purple-800 shrink-0">
                                  Formula Jaccard: {alt.formula_similarity_percent}%
                                </div>
                              )}
                            </div>

                            {alt.differing_actives.length > 0 && (
                              <div className="text-[11px] text-gray-500 pl-1">
                                <span className="font-semibold text-gray-700">Unique to this alternative: </span>
                                <span>{alt.differing_actives.join(', ')}</span>
                              </div>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Modal Footer */}
            <div className="p-4 bg-gray-50 border-t border-gray-200 flex items-center justify-between shrink-0">
              <span className="text-xs text-gray-400">
                SkinSync Production Telemetry Database • WAL Mode Concurrency
              </span>
              <button
                onClick={() => setSelectedProductId(null)}
                className="px-5 py-2 bg-gray-200 hover:bg-gray-300 text-gray-800 font-bold rounded-xl transition cursor-pointer"
              >
                Close Forensics
              </button>
            </div>

          </div>
        </div>
      )}

    </div>
  );
};

export default AdminObservatory;
