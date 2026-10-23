import React, { useState, useEffect, useRef } from 'react';
import { 
  Database, Search, Filter, ShieldCheck, ChevronLeft, ChevronRight, 
  ExternalLink, Eye, X, Loader2, Store, BadgePercent, TrendingDown, 
  Check, AlertTriangle, Activity, Sparkles, CheckCircle2, ArrowRight,
  RefreshCw, SlidersHorizontal, RotateCcw, ArrowUpDown, Tag, DollarSign,
  Layers, Settings2
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
  const [brandFilter, setBrandFilter] = useState('');
  const [skincareFilter, setSkincareFilter] = useState('skincare'); // 'skincare' (default), 'all', 'suppressed'
  const [categoryFilter, setCategoryFilter] = useState('');
  const [availabilityFilter, setAvailabilityFilter] = useState(''); // '', 'in_stock', 'out_of_stock'
  const [hasIngredientsFilter, setHasIngredientsFilter] = useState('');
  const [ingredientSourceFilter, setIngredientSourceFilter] = useState(''); // '', 'store_description', 'cross_store_linked', 'v10_curated'
  const [multiStoreFilter, setMultiStoreFilter] = useState('');
  const [gateFilter, setGateFilter] = useState('');
  const [minPrice, setMinPrice] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [sortBy, setSortBy] = useState('id');
  const [sortOrder, setSortOrder] = useState('asc');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  // Brands list from backend
  const [brandsList, setBrandsList] = useState([]);
  const [loadingBrands, setLoadingBrands] = useState(false);

  // Pipeline Action states
  const [actionLoading, setActionLoading] = useState(null); // null, 'linker', 'classifier', 'refresh'
  const [actionMessage, setActionMessage] = useState(null); // { type: 'success' | 'error', text: string }

  // Telemetry Modal States
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [telemetry, setTelemetry] = useState(null);
  const [loadingTelemetry, setLoadingTelemetry] = useState(false);
  const [activeTab, setActiveTab] = useState('scraped'); // 'scraped', 'ingredients', 'inci', 'pricing', 'dermo', 'similar'

  const searchDebounceRef = useRef(null);

  // Load Overview Data & Brands
  useEffect(() => {
    fetchOverview();
    fetchBrands();
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

  const fetchBrands = async () => {
    try {
      setLoadingBrands(true);
      const res = await fetch('http://127.0.0.1:8000/admin/brands?limit=250');
      if (res.ok) {
        const data = await res.json();
        setBrandsList(data);
      }
    } catch (err) {
      console.error('Failed to load brands:', err);
    } finally {
      setLoadingBrands(false);
    }
  };

  // Load Products with all active filters
  const fetchProducts = async (currentPage = page, query = searchQuery) => {
    try {
      setLoadingProducts(true);
      const params = new URLSearchParams();
      params.append('limit', pageSize.toString());
      params.append('offset', (currentPage * pageSize).toString());

      if (query.trim()) params.append('q', query.trim());
      if (selectedStore) params.append('store_id', selectedStore);
      if (brandFilter) params.append('brand', brandFilter);
      if (skincareFilter === 'skincare') params.append('is_skincare', 'true');
      if (skincareFilter === 'suppressed') params.append('is_skincare', 'false');
      if (categoryFilter) params.append('category', categoryFilter);
      if (availabilityFilter === 'in_stock') params.append('is_available', 'true');
      if (availabilityFilter === 'out_of_stock') params.append('is_available', 'false');
      if (hasIngredientsFilter === 'yes') params.append('has_ingredients', 'true');
      if (hasIngredientsFilter === 'no') params.append('has_ingredients', 'false');
      if (ingredientSourceFilter) params.append('ingredient_source', ingredientSourceFilter);
      if (multiStoreFilter === 'multi') params.append('is_multi_store', 'true');
      if (multiStoreFilter === 'single') params.append('is_multi_store', 'false');
      if (gateFilter) params.append('gate_status', gateFilter);
      if (minPrice !== '') params.append('min_price', minPrice.toString());
      if (maxPrice !== '') params.append('max_price', maxPrice.toString());
      if (sortBy) params.append('sort_by', sortBy);
      if (sortOrder) params.append('order', sortOrder);

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

  // Re-fetch when any filter or pagination changes
  useEffect(() => {
    fetchProducts(page, searchQuery);
  }, [
    page, pageSize, selectedStore, brandFilter, skincareFilter, categoryFilter, 
    availabilityFilter, hasIngredientsFilter, ingredientSourceFilter, multiStoreFilter, 
    gateFilter, minPrice, maxPrice, sortBy, sortOrder
  ]);

  // Handle Search Input with Debounce
  const handleSearchChange = (val) => {
    setSearchQuery(val);
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      setPage(0);
      fetchProducts(0, val);
    }, 350);
  };

  // Clear All Filters
  const handleClearAllFilters = () => {
    setSearchQuery('');
    setSelectedStore('');
    setBrandFilter('');
    setCategoryFilter('');
    setSkincareFilter('skincare');
    setAvailabilityFilter('');
    setHasIngredientsFilter('');
    setIngredientSourceFilter('');
    setMultiStoreFilter('');
    setGateFilter('');
    setMinPrice('');
    setMaxPrice('');
    setSortBy('id');
    setSortOrder('asc');
    setPage(0);
  };

  // Quick Preset Filters
  const handlePreset = (preset) => {
    setPage(0);
    if (preset === 'all') {
      setSkincareFilter('all');
      setMultiStoreFilter('');
      setHasIngredientsFilter('');
      setGateFilter('');
      setSelectedStore('');
    } else if (preset === 'skincare') {
      setSkincareFilter('skincare');
      setMultiStoreFilter('');
      setGateFilter('');
    } else if (preset === 'suppressed') {
      setSkincareFilter('suppressed');
      setMultiStoreFilter('');
      setGateFilter('');
    } else if (preset === 'multistore') {
      setSkincareFilter('all');
      setMultiStoreFilter('multi');
    } else if (preset === 'missing_ing') {
      setHasIngredientsFilter('no');
    } else if (preset === 'verified_inci') {
      setGateFilter('gemini_approved');
    } else if (preset === 'pending_ai') {
      setGateFilter('pending_gemini');
    }
  };

  // Manual Classification Override Handler
  const handleUpdateClassification = async (productId, newIsSkincare, newCategory) => {
    try {
      const payload = {};
      if (newIsSkincare !== undefined) payload.is_skincare = newIsSkincare;
      if (newCategory !== undefined) payload.category = newCategory;

      const res = await fetch(`http://127.0.0.1:8000/admin/products/${productId}/classification`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const data = await res.json();
        const updated = data.product;
        // Update product in table list
        setProducts(prev => prev.map(p => p.id === productId ? { ...p, is_skincare: updated.is_skincare, category: updated.category } : p));
        // Update telemetry if open
        if (telemetry && telemetry.product.id === productId) {
          setTelemetry(prev => ({
            ...prev,
            product: { ...prev.product, is_skincare: updated.is_skincare, category: updated.category }
          }));
        }
        fetchOverview();
        setActionMessage({
          type: 'success',
          text: `Product #${productId} classification saved: ${updated.is_skincare ? 'Active Skincare' : 'Suppressed'} • ${updated.category}`
        });
        setTimeout(() => setActionMessage(null), 4000);
      }
    } catch (err) {
      console.error('Failed to update classification:', err);
      setActionMessage({ type: 'error', text: 'Failed to update product classification.' });
      setTimeout(() => setActionMessage(null), 4000);
    }
  };

  // Pipeline Action Triggers
  const handleTriggerLinker = async () => {
    try {
      setActionLoading('linker');
      const res = await fetch('http://127.0.0.1:8000/admin/pipeline/trigger-linker', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setActionMessage({
          type: 'success',
          text: `Product Linker Complete: ${data.multi_store_clusters} multi-store clusters formed, ${data.ingredients_propagated} ingredient lists filled across stores!`
        });
        fetchOverview();
        fetchProducts(page, searchQuery);
      }
    } catch (err) {
      console.error('Trigger linker failed:', err);
      setActionMessage({ type: 'error', text: 'Failed to run cross-store product linker.' });
    } finally {
      setActionLoading(null);
      setTimeout(() => setActionMessage(null), 6000);
    }
  };

  const handleTriggerClassifier = async () => {
    try {
      setActionLoading('classifier');
      const res = await fetch('http://127.0.0.1:8000/admin/pipeline/trigger-classifier', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setActionMessage({
          type: 'success',
          text: `Taxonomy Classifier Complete: ${data.total_skincare_active} active skincare products, ${data.total_suppressed} suppressed non-skincare!`
        });
        fetchOverview();
        fetchProducts(page, searchQuery);
      }
    } catch (err) {
      console.error('Trigger classifier failed:', err);
      setActionMessage({ type: 'error', text: 'Failed to run taxonomy classifier.' });
    } finally {
      setActionLoading(null);
      setTimeout(() => setActionMessage(null), 6000);
    }
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

  const activeFiltersCount = [
    searchQuery, selectedStore, brandFilter, categoryFilter, 
    skincareFilter !== 'skincare', availabilityFilter, hasIngredientsFilter, 
    ingredientSourceFilter, multiStoreFilter, gateFilter, minPrice, maxPrice
  ].filter(Boolean).length;

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 font-sans pb-20">
      {/* Top Navigation */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-40 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
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

          <div className="flex items-center gap-2.5 flex-wrap">
            <button
              onClick={handleTriggerLinker}
              disabled={actionLoading !== null}
              className="text-xs font-bold px-3 py-2 rounded-xl border border-purple-200 bg-purple-50 text-purple-700 hover:bg-purple-100 disabled:opacity-50 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
              title="Run 2-Pass Cross-Store Variant Alignment"
            >
              <Layers className={`w-3.5 h-3.5 ${actionLoading === 'linker' ? 'animate-spin' : ''}`} />
              <span>{actionLoading === 'linker' ? 'Linking...' : 'Re-Link Catalog'}</span>
            </button>

            <button
              onClick={handleTriggerClassifier}
              disabled={actionLoading !== null}
              className="text-xs font-bold px-3 py-2 rounded-xl border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
              title="Re-run Rule-based Skincare & Beauty Taxonomy Classifier"
            >
              <Tag className={`w-3.5 h-3.5 ${actionLoading === 'classifier' ? 'animate-spin' : ''}`} />
              <span>{actionLoading === 'classifier' ? 'Classifying...' : 'Re-Run Classifier'}</span>
            </button>

            <button
              onClick={fetchOverview}
              className="text-xs text-gray-600 hover:text-purple-600 font-semibold px-3 py-2 rounded-xl border border-gray-200 bg-gray-50 hover:bg-gray-100 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
            >
              <Activity className="w-3.5 h-3.5" />
              <span>Refresh Stats</span>
            </button>

            <button
              onClick={onBackToApp}
              className="bg-gray-900 hover:bg-purple-600 text-white font-bold text-xs px-4 py-2 rounded-xl transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
            >
              <ChevronLeft className="w-4 h-4" />
              <span>Back to SkinSync App</span>
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 pt-6 space-y-6">
        {/* Action Message Banner */}
        {actionMessage && (
          <div className={`p-4 rounded-2xl border text-xs font-semibold flex items-center justify-between shadow-sm animate-fade-in ${
            actionMessage.type === 'success' 
              ? 'bg-emerald-50 border-emerald-300 text-emerald-800' 
              : 'bg-red-50 border-red-300 text-red-800'
          }`}>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>{actionMessage.text}</span>
            </div>
            <button 
              onClick={() => setActionMessage(null)} 
              className="text-gray-400 hover:text-gray-600 font-bold ml-4 cursor-pointer"
            >
              ✕
            </button>
          </div>
        )}
        
        {/* 1. System Vitals & Ingestion Counters */}
        {overview && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
            <div className="bg-white p-4 rounded-2xl border border-emerald-300 shadow-sm bg-gradient-to-b from-emerald-50/40 to-white">
              <div className="text-[10px] font-bold text-emerald-700 uppercase tracking-wider mb-1">Active Skincare (Phase 1)</div>
              <div className="text-xl font-black text-emerald-700">
                {overview.catalog_vitals.active_skincare_products ? overview.catalog_vitals.active_skincare_products.toLocaleString() : '7,413'}
              </div>
              <div className="text-[10px] text-emerald-600 mt-1 font-semibold">Active in App</div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-amber-200 shadow-sm bg-gradient-to-b from-amber-50/30 to-white">
              <div className="text-[10px] font-bold text-amber-700 uppercase tracking-wider mb-1">Suppressed (Phase 2)</div>
              <div className="text-xl font-black text-amber-800">
                {overview.catalog_vitals.suppressed_non_skincare ? overview.catalog_vitals.suppressed_non_skincare.toLocaleString() : '15,355'}
              </div>
              <div className="text-[10px] text-amber-700 mt-1">Deos, perfumes, hair</div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Total Listings</div>
              <div className="text-xl font-black text-gray-900">
                {overview.catalog_vitals.total_store_listings.toLocaleString()}
              </div>
              <div className="text-[10px] text-gray-500 mt-1">Across 6 retailers</div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Canonical Entities</div>
              <div className="text-xl font-black text-purple-700">
                {overview.catalog_vitals.canonical_entities.toLocaleString()}
              </div>
              <div className="text-[10px] text-gray-500 mt-1">Unified clusters</div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Multi-Store Deals</div>
              <div className="text-xl font-black text-emerald-600">
                {overview.catalog_vitals.multi_store_clusters.toLocaleString()}
              </div>
              <div className="text-[10px] text-emerald-700 mt-1 font-semibold">Price arbitrage</div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Full Ingredients</div>
              <div className="text-xl font-black text-blue-600">
                {overview.catalog_vitals.products_with_ingredients.toLocaleString()}
              </div>
              <div className="text-[10px] text-gray-500 mt-1">
                {overview.catalog_vitals.coverage_percent}% coverage
              </div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">INCI Reference</div>
              <div className="text-xl font-black text-indigo-600">
                {overview.catalog_vitals.inci_matched_products.toLocaleString()}
              </div>
              <div className="text-[10px] text-gray-500 mt-1">SkinSafe DB</div>
            </div>

            <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">AI Verified</div>
              <div className="text-xl font-black text-pink-600">
                {overview.catalog_vitals.ai_verified_products.toLocaleString()}
              </div>
              <div className="text-[10px] text-gray-500 mt-1">Cross-Encoder/LLM</div>
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

            {/* Quick Presets Bar */}
            <div className="flex items-center gap-1.5 flex-wrap pt-1 text-xs">
              <span className="text-gray-400 font-bold uppercase text-[10px] mr-1 flex items-center gap-1">
                <SlidersHorizontal className="w-3 h-3 text-purple-600" /> Presets:
              </span>
              <button
                onClick={() => handlePreset('skincare')}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  skincareFilter === 'skincare' && !multiStoreFilter && !hasIngredientsFilter && !gateFilter
                    ? 'bg-emerald-600 text-white shadow-sm'
                    : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border border-emerald-200'
                }`}
              >
                🧴 Skincare (Phase 1)
              </button>
              <button
                onClick={() => handlePreset('suppressed')}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  skincareFilter === 'suppressed'
                    ? 'bg-amber-600 text-white shadow-sm'
                    : 'bg-amber-50 text-amber-800 hover:bg-amber-100 border border-amber-200'
                }`}
              >
                📦 Suppressed (Phase 2)
              </button>
              <button
                onClick={() => handlePreset('multistore')}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  multiStoreFilter === 'multi'
                    ? 'bg-pink-600 text-white shadow-sm'
                    : 'bg-pink-50 text-pink-700 hover:bg-pink-100 border border-pink-200'
                }`}
              >
                🏪 Multi-Store Deals
              </button>
              <button
                onClick={() => handlePreset('verified_inci')}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  gateFilter === 'gemini_approved'
                    ? 'bg-purple-600 text-white shadow-sm'
                    : 'bg-purple-50 text-purple-700 hover:bg-purple-100 border border-purple-200'
                }`}
              >
                🎯 AI Verified INCI
              </button>
              <button
                onClick={() => handlePreset('missing_ing')}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  hasIngredientsFilter === 'no'
                    ? 'bg-gray-800 text-white shadow-sm'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200 border border-gray-200'
                }`}
              >
                ⚠️ Missing INCI
              </button>
              <button
                onClick={() => handlePreset('all')}
                className={`px-2.5 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  skincareFilter === 'all' && !multiStoreFilter && !hasIngredientsFilter
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100 border border-indigo-200'
                }`}
              >
                🌐 Entire Catalog
              </button>
              {activeFiltersCount > 0 && (
                <button
                  onClick={handleClearAllFilters}
                  className="px-2.5 py-1 rounded-lg text-xs font-bold bg-red-50 text-red-600 hover:bg-red-100 border border-red-200 transition-all flex items-center gap-1 cursor-pointer ml-auto"
                >
                  <RotateCcw className="w-3 h-3" />
                  <span>Reset All ({activeFiltersCount})</span>
                </button>
              )}
            </div>

            {/* Comprehensive Multi-Filter Grid */}
            <div className="space-y-3 pt-2">
              {/* Row 1: Search, Brand, Skincare Scope, Category */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {/* Search Query */}
                <div className="relative">
                  <Search className="w-4 h-4 text-gray-400 absolute left-3 top-2.5" />
                  <input
                    type="text"
                    placeholder="Search title, brand, handle..."
                    value={searchQuery}
                    onChange={(e) => handleSearchChange(e.target.value)}
                    className="w-full pl-9 pr-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 focus:bg-white transition-all font-medium"
                  />
                </div>

                {/* Brand Filter */}
                <select
                  value={brandFilter}
                  onChange={(e) => { setBrandFilter(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 font-medium"
                >
                  <option value="">All Brands ({brandsList.length} indexed)</option>
                  {brandsList.map((b) => (
                    <option key={b.brand} value={b.brand}>
                      {b.brand} ({b.skincare_count} active / {b.total_listings})
                    </option>
                  ))}
                </select>

                {/* Skincare Scope Toggle */}
                <select
                  value={skincareFilter}
                  onChange={(e) => { setSkincareFilter(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-emerald-50/70 border border-emerald-300 text-emerald-950 font-bold rounded-xl text-xs focus:outline-none focus:border-emerald-500"
                >
                  <option value="skincare">🧴 Active Skincare Only (Phase 1)</option>
                  <option value="all">🌐 All Catalog (Skincare + Beauty)</option>
                  <option value="suppressed">📦 Suppressed Non-Skincare (Phase 2)</option>
                </select>

                {/* Category Filter */}
                <select
                  value={categoryFilter}
                  onChange={(e) => { setCategoryFilter(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 font-medium"
                >
                  <option value="">All Categories</option>
                  <optgroup label="Active Skincare & Body">
                    <option value="Cleanser">Cleanser</option>
                    <option value="Moisturizer">Moisturizer</option>
                    <option value="Serum">Serum</option>
                    <option value="Sunscreen">Sunscreen</option>
                    <option value="Tanning">Tanning & After-Sun</option>
                    <option value="Body Care">Body Care</option>
                    <option value="Toner">Toner</option>
                    <option value="Exfoliator">Exfoliator</option>
                    <option value="Eye Skincare">Eye Skincare</option>
                    <option value="Mask">Mask</option>
                    <option value="Lip Care">Lip Care</option>
                    <option value="Treatment">Treatment</option>
                  </optgroup>
                  <optgroup label="Non-Skincare (Suppressed)">
                    <option value="Deodorant">Deodorant / Antiperspirant</option>
                    <option value="Fragrance">Fragrance / Perfume</option>
                    <option value="Hair Care">Hair Care</option>
                    <option value="Makeup">Makeup</option>
                    <option value="Nail Care">Nail Care</option>
                    <option value="Supplements & Health">Supplements & Health (Vitamins/Pills)</option>
                    <option value="Medical Supplies">Medical Supplies & First Aid</option>
                    <option value="Oral Care">Oral Care</option>
                    <option value="Tools & Accessories">Tools & Accessories</option>
                    <option value="Baby Care">Baby Care</option>
                    <option value="Set">Set / Bundle</option>
                    <option value="Other Non-Skincare">Other Non-Skincare</option>
                  </optgroup>
                </select>
              </div>

              {/* Row 2: Retail Store, Availability, Ingredient Source, Gate Status */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {/* Store Filter */}
                <select
                  value={selectedStore}
                  onChange={(e) => { setSelectedStore(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 font-medium"
                >
                  <option value="">All Stores (6 Retailers)</option>
                  <option value="sourcebeauty">Source Beauty</option>
                  <option value="feel22">Feel22 Egypt</option>
                  <option value="bloompharmacy">Bloom Pharmacy</option>
                  <option value="drdawood">Dr Dawood Pharmacy</option>
                  <option value="bashrety">Bashrety</option>
                  <option value="rawafrican">Raw African</option>
                </select>

                {/* Stock Availability */}
                <select
                  value={availabilityFilter}
                  onChange={(e) => { setAvailabilityFilter(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 font-medium"
                >
                  <option value="">Stock: All Items</option>
                  <option value="in_stock">🟢 In Stock Only</option>
                  <option value="out_of_stock">🔴 Out of Stock Only</option>
                </select>

                {/* Ingredient Source */}
                <select
                  value={ingredientSourceFilter}
                  onChange={(e) => { setIngredientSourceFilter(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 font-medium"
                >
                  <option value="">Ingredients: All Sources</option>
                  <option value="store_description">Store Scraped HTML</option>
                  <option value="cross_store_linked">Cross-Store Clustered</option>
                  <option value="v10_curated">SkinSafe / Curated DB</option>
                </select>

                {/* Gate Status */}
                <select
                  value={gateFilter}
                  onChange={(e) => { setGateFilter(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 font-medium"
                >
                  <option value="">INCI Status (All)</option>
                  <option value="gemini_approved">Gemini Approved</option>
                  <option value="auto_approved">Auto Approved (Cross-Encoder)</option>
                  <option value="pending_gemini">Pending Gemini Verification</option>
                  <option value="cross_rejected">Cross-Encoder Rejected</option>
                  <option value="rejected_safeguard">Rejected by Cosmetic Safeguard</option>
                </select>
              </div>

              {/* Row 3: Price Min/Max, Sort By, Sort Order, Page Size */}
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                {/* Min Price */}
                <div className="relative">
                  <span className="text-[10px] text-gray-400 absolute left-2.5 top-2.5 font-bold">Min</span>
                  <input
                    type="number"
                    placeholder="0 EGP"
                    value={minPrice}
                    onChange={(e) => { setMinPrice(e.target.value); setPage(0); }}
                    className="w-full pl-9 pr-2 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 focus:bg-white transition-all font-medium"
                  />
                </div>

                {/* Max Price */}
                <div className="relative">
                  <span className="text-[10px] text-gray-400 absolute left-2.5 top-2.5 font-bold">Max</span>
                  <input
                    type="number"
                    placeholder="Max EGP"
                    value={maxPrice}
                    onChange={(e) => { setMaxPrice(e.target.value); setPage(0); }}
                    className="w-full pl-9 pr-2 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 focus:bg-white transition-all font-medium"
                  />
                </div>

                {/* Multi Store Presence */}
                <select
                  value={multiStoreFilter}
                  onChange={(e) => { setMultiStoreFilter(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-purple-500 font-medium"
                >
                  <option value="">Store Presence</option>
                  <option value="multi">Multi-Store (Arbitrage)</option>
                  <option value="single">Single Store Only</option>
                </select>

                {/* Sort By */}
                <select
                  value={sortBy}
                  onChange={(e) => { setSortBy(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-purple-50/60 border border-purple-200 text-purple-900 rounded-xl text-xs font-semibold focus:outline-none focus:border-purple-500"
                >
                  <option value="id">Sort: Database ID</option>
                  <option value="title">Sort: Product Title</option>
                  <option value="brand">Sort: Brand Name</option>
                  <option value="price">Sort: Price (EGP)</option>
                  <option value="store_count">Sort: Multi-Store Count</option>
                  <option value="has_ingredients">Sort: Has Ingredients</option>
                </select>

                {/* Sort Order */}
                <select
                  value={sortOrder}
                  onChange={(e) => { setSortOrder(e.target.value); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-medium focus:outline-none focus:border-purple-500"
                >
                  <option value="asc">Ascending (A-Z / Low-High)</option>
                  <option value="desc">Descending (Z-A / High-Low)</option>
                </select>

                {/* Page Size */}
                <select
                  value={pageSize}
                  onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
                  className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-medium focus:outline-none focus:border-purple-500"
                >
                  <option value={20}>20 per page</option>
                  <option value={50}>50 per page</option>
                  <option value={100}>100 per page</option>
                  <option value={250}>250 per page</option>
                </select>
              </div>
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
                  <tr className="bg-gray-50/80 border-b border-gray-200 text-gray-500 uppercase font-extrabold text-[10px] tracking-wider select-none">
                    <th 
                      className="py-3 px-4 cursor-pointer hover:text-purple-700 transition-colors"
                      onClick={() => { setSortBy('title'); setSortOrder(sortBy === 'title' && sortOrder === 'asc' ? 'desc' : 'asc'); }}
                    >
                      <div className="flex items-center gap-1">
                        <span>Product</span>
                        <ArrowUpDown className={`w-3 h-3 ${sortBy === 'title' ? 'text-purple-600' : 'text-gray-400'}`} />
                      </div>
                    </th>
                    <th className="py-3 px-4">Store</th>
                    <th 
                      className="py-3 px-4 cursor-pointer hover:text-purple-700 transition-colors"
                      onClick={() => { setSortBy('price'); setSortOrder(sortBy === 'price' && sortOrder === 'asc' ? 'desc' : 'asc'); }}
                    >
                      <div className="flex items-center gap-1">
                        <span>Price</span>
                        <ArrowUpDown className={`w-3 h-3 ${sortBy === 'price' ? 'text-purple-600' : 'text-gray-400'}`} />
                      </div>
                    </th>
                    <th className="py-3 px-4">Ingredients Source</th>
                    <th 
                      className="py-3 px-4 cursor-pointer hover:text-purple-700 transition-colors"
                      onClick={() => { setSortBy('store_count'); setSortOrder(sortBy === 'store_count' && sortOrder === 'asc' ? 'desc' : 'asc'); }}
                    >
                      <div className="flex items-center gap-1">
                        <span>Cluster / Stores</span>
                        <ArrowUpDown className={`w-3 h-3 ${sortBy === 'store_count' ? 'text-purple-600' : 'text-gray-400'}`} />
                      </div>
                    </th>
                    <th 
                      className="py-3 px-4 cursor-pointer hover:text-purple-700 transition-colors"
                      onClick={() => { setSortBy('has_ingredients'); setSortOrder(sortBy === 'has_ingredients' && sortOrder === 'asc' ? 'desc' : 'asc'); }}
                    >
                      <div className="flex items-center gap-1">
                        <span>INCI Status</span>
                        <ArrowUpDown className={`w-3 h-3 ${sortBy === 'has_ingredients' ? 'text-purple-600' : 'text-gray-400'}`} />
                      </div>
                    </th>
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
                            <div className="text-[10px] font-bold text-purple-600 uppercase flex items-center gap-1.5">
                              <span>{p.brand}</span>
                              {p.is_skincare ? (
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleUpdateClassification(p.id, 0, p.category); }}
                                  title="Click to suppress this product (move to Phase 2 non-skincare)"
                                  className="bg-emerald-50 hover:bg-emerald-100 text-emerald-700 font-bold px-1.5 py-0.5 rounded border border-emerald-200 text-[9px] transition-colors cursor-pointer"
                                >
                                  🧴 Skincare
                                </button>
                              ) : (
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleUpdateClassification(p.id, 1, p.category); }}
                                  title="Click to activate this product as Phase 1 skincare"
                                  className="bg-amber-50 hover:bg-amber-100 text-amber-700 font-bold px-1.5 py-0.5 rounded border border-amber-200 text-[9px] transition-colors cursor-pointer"
                                >
                                  📦 Suppressed
                                </button>
                              )}
                            </div>
                            <div className="font-bold text-gray-900 truncate" title={p.title}>
                              {p.title}
                            </div>
                            <div className="text-[10px] text-gray-400 truncate flex items-center gap-1.5 mt-0.5">
                              <span>ID: #{p.id}</span>
                              <span>•</span>
                              <span className="font-medium text-gray-600 bg-gray-100 px-1.5 py-0.5 rounded text-[10px]">{p.category}</span>
                            </div>
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

              <div className="flex items-center gap-2 mb-2 flex-wrap">
                <span className="bg-purple-500/30 text-purple-200 text-[10px] uppercase font-black px-2.5 py-0.5 rounded-full border border-purple-400/30">
                  Forensic Audit Trail
                </span>
                {telemetry && (
                  <>
                    <span className="bg-white/10 text-white text-[10px] font-bold px-2 py-0.5 rounded">
                      Store: {telemetry.product.store_name}
                    </span>
                    {telemetry.product.is_skincare ? (
                      <span className="bg-emerald-500/30 text-emerald-200 text-[10px] font-bold px-2.5 py-0.5 rounded-full border border-emerald-400/30 inline-flex items-center gap-1">
                        🧴 Active Skincare (Phase 1)
                      </span>
                    ) : (
                      <span className="bg-amber-500/30 text-amber-200 text-[10px] font-bold px-2.5 py-0.5 rounded-full border border-amber-400/30 inline-flex items-center gap-1">
                        📦 Suppressed Non-Skincare (Phase 2)
                      </span>
                    )}
                    <span className="bg-indigo-500/30 text-indigo-200 text-[10px] font-bold px-2.5 py-0.5 rounded-full border border-indigo-400/30">
                      Category: {telemetry.product.category}
                    </span>
                  </>
                )}
              </div>

              <h3 className="text-xl font-black leading-tight">
                {telemetry ? telemetry.product.title : 'Loading Product Details...'}
              </h3>
              {telemetry && (
                <p className="text-purple-200 text-xs mt-1">
                  Brand: {telemetry.product.brand} • Price: {telemetry.product.price} EGP
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
                      {/* ADMIN CLASSIFICATION OVERRIDE PANEL (Exclusively on Tab 1) */}
                      {telemetry && (
                        <div className="p-4 bg-gradient-to-r from-gray-50 to-purple-50/40 border border-gray-200 rounded-2xl shadow-sm">
                          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-gray-200/80">
                            <div className="flex items-center gap-2">
                              <Settings2 className="w-4 h-4 text-purple-600" />
                              <span className="font-extrabold text-xs text-gray-900 uppercase tracking-wide">
                                Admin Classification & Override Controls
                              </span>
                            </div>
                            <div className="flex items-center gap-2">
                              <button
                                onClick={() => handleUpdateClassification(telemetry.product.id, 1, telemetry.product.category)}
                                className={`px-3 py-1.5 rounded-xl font-bold text-xs transition-all flex items-center gap-1.5 cursor-pointer ${
                                  telemetry.product.is_skincare
                                    ? 'bg-emerald-600 text-white shadow-sm ring-2 ring-emerald-400'
                                    : 'bg-white text-gray-600 border border-gray-200 hover:bg-emerald-50 hover:text-emerald-700'
                                }`}
                              >
                                <span>🧴 Active Skincare (Phase 1)</span>
                              </button>
                              <button
                                onClick={() => handleUpdateClassification(telemetry.product.id, 0, telemetry.product.category)}
                                className={`px-3 py-1.5 rounded-xl font-bold text-xs transition-all flex items-center gap-1.5 cursor-pointer ${
                                  !telemetry.product.is_skincare
                                    ? 'bg-amber-600 text-white shadow-sm ring-2 ring-amber-400'
                                    : 'bg-white text-gray-600 border border-gray-200 hover:bg-amber-50 hover:text-amber-800'
                                }`}
                              >
                                <span>📦 Suppressed (Phase 2)</span>
                              </button>
                            </div>
                          </div>

                          <div className="pt-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
                            <div className="text-gray-500 text-[11px]">
                              {telemetry.product.is_skincare ? (
                                <span className="text-emerald-700 font-semibold flex items-center gap-1">
                                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                                  Active in consumer search, recommendations, and INCI ingredient routines.
                                </span>
                              ) : (
                                <span className="text-amber-700 font-semibold flex items-center gap-1">
                                  <AlertTriangle className="w-3.5 h-3.5 text-amber-600" />
                                  Suppressed from Phase 1 consumer search; preserved in catalog for Phase 2 beauty catalog.
                                </span>
                              )}
                            </div>

                            <div className="flex items-center gap-2">
                              <span className="text-gray-500 font-semibold text-[11px] whitespace-nowrap">Reassign Category:</span>
                              <select
                                value={telemetry.product.category}
                                onChange={(e) => handleUpdateClassification(telemetry.product.id, telemetry.product.is_skincare ? 1 : 0, e.target.value)}
                                className="px-2.5 py-1 bg-white border border-gray-200 rounded-lg text-xs font-semibold focus:outline-none focus:border-purple-500 text-gray-800 cursor-pointer"
                              >
                                <optgroup label="Active Skincare & Body">
                                  <option value="Cleanser">Cleanser</option>
                                  <option value="Moisturizer">Moisturizer</option>
                                  <option value="Serum">Serum</option>
                                  <option value="Sunscreen">Sunscreen</option>
                                  <option value="Tanning">Tanning & After-Sun</option>
                                  <option value="Body Care">Body Care</option>
                                  <option value="Toner">Toner</option>
                                  <option value="Exfoliator">Exfoliator</option>
                                  <option value="Eye Skincare">Eye Skincare</option>
                                  <option value="Mask">Mask</option>
                                  <option value="Lip Care">Lip Care</option>
                                  <option value="Treatment">Treatment</option>
                                </optgroup>
                                <optgroup label="Non-Skincare (Suppressed)">
                                  <option value="Deodorant">Deodorant / Antiperspirant</option>
                                  <option value="Fragrance">Fragrance / Perfume</option>
                                  <option value="Hair Care">Hair Care</option>
                                  <option value="Makeup">Makeup</option>
                                  <option value="Nail Care">Nail Care</option>
                                  <option value="Supplements & Health">Supplements & Health</option>
                                  <option value="Medical Supplies">Medical Supplies</option>
                                  <option value="Oral Care">Oral Care</option>
                                  <option value="Tools & Accessories">Tools & Accessories</option>
                                  <option value="Baby Care">Baby Care</option>
                                  <option value="Set">Set / Bundle</option>
                                  <option value="Other Non-Skincare">Other Non-Skincare</option>
                                </optgroup>
                              </select>
                            </div>
                          </div>
                        </div>
                      )}

                      {telemetry && !telemetry.product.is_skincare && (
                        <div className="p-4 bg-amber-50 border border-amber-200 rounded-2xl flex items-start gap-3 text-amber-900">
                          <div className="text-xl shrink-0">📦</div>
                          <div>
                            <div className="font-extrabold text-xs text-amber-950 flex items-center gap-2">
                              <span>Suppressed Non-Skincare Item (Phase 2 Catalog)</span>
                              <span className="bg-amber-200 text-amber-900 text-[10px] px-2 py-0.5 rounded-full font-bold">
                                {telemetry.product.category}
                              </span>
                            </div>
                            <p className="text-[11px] text-amber-800/90 mt-1 leading-relaxed">
                              This product is categorized outside active skincare (e.g., deodorant, perfume, hair care, makeup).
                              It is preserved in the database for future beauty expansion, but intentionally suppressed from Phase 1 consumer search, recommendation engines, and INCI ingredient matching workflows.
                            </p>
                          </div>
                        </div>
                      )}

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
                      {/* INCI PIPELINE DECISION & POLICY CARD */}
                      <div className="p-5 bg-gradient-to-br from-white to-gray-50 rounded-2xl border border-gray-200 shadow-sm space-y-4">
                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-gray-200">
                          <div>
                            <div className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">
                              INCI Pipeline Decision & Policy Status
                            </div>
                            <div className="font-extrabold text-base text-gray-900 mt-0.5">
                              {telemetry.inci_matching_telemetry.decision_summary || 'Evaluation Complete'}
                            </div>
                          </div>
                          <div>
                            {telemetry.inci_matching_telemetry.has_source_ingredients ? (
                              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-100 text-emerald-800 rounded-xl font-black text-xs border border-emerald-300">
                                <ShieldCheck className="w-4 h-4 text-emerald-600" />
                                <span>Ground Truth Preserved (LLM Calls Prohibited)</span>
                              </span>
                            ) : telemetry.inci_matching_telemetry.inci_matched_id ? (
                              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-purple-100 text-purple-800 rounded-xl font-black text-xs border border-purple-300">
                                <CheckCircle2 className="w-4 h-4 text-purple-600" />
                                <span>Approved INCI Match</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-amber-100 text-amber-800 rounded-xl font-bold text-xs border border-amber-300">
                                <AlertTriangle className="w-4 h-4 text-amber-600" />
                                <span>{telemetry.inci_matching_telemetry.gates?.gate4_gemini_status || 'Pending / Rejected'}</span>
                              </span>
                            )}
                          </div>
                        </div>

                        {telemetry.inci_matching_telemetry.has_source_ingredients ? (
                          <div className="p-3.5 bg-emerald-50/80 border border-emerald-200 rounded-xl flex items-start gap-2.5 text-xs text-emerald-900">
                            <span className="text-base shrink-0">🛡️</span>
                            <div className="leading-relaxed">
                              <span className="font-bold">Authentic Source Ingredients Active: </span>
                              This product already possesses genuine ingredients scraped directly from the store. The pipeline strictly preserves authentic source ingredients as ground truth and will <span className="font-bold underline">never overwrite</span> them with INCI database formulations. External LLM calls (Gemini/Jev) are <span className="font-bold text-emerald-950">strictly prohibited</span>; local Gates 1–4 are used for fast, zero-cost reference alignment only.
                            </div>
                          </div>
                        ) : (
                          <div className="p-3.5 bg-purple-50/70 border border-purple-200 rounded-xl text-xs text-purple-900 leading-relaxed">
                            <span className="font-bold">INCI Formulation Alignment: </span>
                            This product does not have authentic retailer ingredients. It is evaluated against the 21,000+ formula INCI knowledge base using hybrid dense/sparse retrieval and multi-gate cross-encoder validation to determine formulation identity.
                          </div>
                        )}

                        {(telemetry.inci_matching_telemetry.decision_reason || telemetry.inci_matching_telemetry.safeguard_log) && (
                          <div className="text-xs">
                            <div className="text-[10px] font-bold text-gray-500 uppercase mb-1">Decision Rationale & Safeguard Notes:</div>
                            <div className="p-3 bg-gray-100 rounded-xl font-mono text-[11px] text-gray-800 whitespace-pre-wrap max-h-24 overflow-y-auto">
                              {telemetry.inci_matching_telemetry.decision_reason || telemetry.inci_matching_telemetry.safeguard_log}
                            </div>
                          </div>
                        )}
                      </div>

                      {/* TOP EVALUATED INCI CANDIDATE */}
                      <div className="p-5 bg-white rounded-2xl border border-gray-200 shadow-sm space-y-3">
                        <div className="flex items-center justify-between pb-2 border-b border-gray-100">
                          <div className="flex items-center gap-2">
                            <span className="text-base">🎯</span>
                            <div>
                              <div className="font-extrabold text-sm text-gray-900">Top Evaluated INCI Candidate</div>
                              <div className="text-[10px] text-gray-400">Forensic match evaluated against product title & brand</div>
                            </div>
                          </div>
                          {telemetry.inci_matching_telemetry.top_candidate?.id && (
                            <span className="bg-gray-100 text-gray-700 px-2.5 py-1 rounded-lg font-mono text-xs font-bold">
                              Reference ID #{telemetry.inci_matching_telemetry.top_candidate.id}
                            </span>
                          )}
                        </div>

                        {telemetry.inci_matching_telemetry.top_candidate ? (
                          <div className="space-y-3">
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                              <div>
                                <span className="text-[10px] font-bold uppercase text-purple-600">
                                  {telemetry.inci_matching_telemetry.top_candidate.brand || 'Brand Unknown'}
                                </span>
                                <h4 className="font-black text-sm text-gray-900">
                                  {telemetry.inci_matching_telemetry.top_candidate.product_name}
                                </h4>
                              </div>
                              <div className="flex items-center gap-2 text-xs">
                                <span className="px-2.5 py-1 bg-gray-100 text-gray-700 rounded-lg font-semibold text-[11px]">
                                  Database: {telemetry.inci_matching_telemetry.top_candidate.source || 'inci_reference'}
                                </span>
                                {telemetry.inci_matching_telemetry.top_candidate.parsed_tokens_count && (
                                  <span className="px-2.5 py-1 bg-purple-50 text-purple-700 rounded-lg font-semibold text-[11px]">
                                    {telemetry.inci_matching_telemetry.top_candidate.parsed_tokens_count} tokens
                                  </span>
                                )}
                              </div>
                            </div>

                            {/* Candidate Actives */}
                            {telemetry.inci_matching_telemetry.top_candidate.actives && telemetry.inci_matching_telemetry.top_candidate.actives.length > 0 && (
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span className="text-[10px] font-bold text-gray-400 uppercase">Actives:</span>
                                {telemetry.inci_matching_telemetry.top_candidate.actives.map((act, i) => (
                                  <span key={i} className="px-2 py-0.5 bg-purple-50 text-purple-700 font-bold rounded text-[11px]">
                                    {act}
                                  </span>
                                ))}
                              </div>
                            )}

                            {/* Candidate Raw Ingredients */}
                            <div>
                              <div className="text-[10px] font-bold text-gray-400 uppercase mb-1">
                                Candidate Ingredients Formulation:
                              </div>
                              <div className="p-3 bg-gray-50 rounded-xl text-[11px] font-mono text-gray-800 break-words max-h-32 overflow-y-auto border border-gray-100 leading-relaxed">
                                {telemetry.inci_matching_telemetry.top_candidate.ingredients_raw || 'No raw formulation string recorded in reference record.'}
                              </div>
                            </div>
                          </div>
                        ) : (
                          <div className="py-6 text-center text-gray-400 italic">
                            No INCI candidate exceeded preliminary gate criteria for this product title.
                          </div>
                        )}
                      </div>

                      {/* Gate Status Grid */}
                      <div>
                        <div className="font-bold text-gray-700 mb-2 uppercase text-[10px] tracking-wider">
                          4-Gate Evaluation Pipeline Telemetry
                        </div>
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

                                  <div className="flex items-center gap-3">
                                    <div className="text-right">
                                      <div className="font-black text-sm text-gray-900">{offer.price} EGP</div>
                                      <div className="text-[10px] text-gray-400 capitalize">{offer.ingredient_source || 'store'}</div>
                                    </div>
                                    {!offer.is_current_product && (
                                      <button
                                        type="button"
                                        onClick={() => handleOpenTelemetry(offer.product_id)}
                                        className="px-2.5 py-1.5 bg-purple-50 hover:bg-purple-100 text-purple-700 font-bold rounded-lg text-xs transition cursor-pointer"
                                        title="Inspect listing forensics"
                                      >
                                        Inspect
                                      </button>
                                    )}
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
                          <div className="space-y-3">
                            <div className="font-bold text-gray-700 uppercase text-[10px] tracking-wider">
                              Comedogenic & Irritating Ingredients Flagged
                            </div>
                            
                            {/* Comedogenic Ingredients */}
                            {telemetry.clinical_dermatology.comedogenic_ingredients && telemetry.clinical_dermatology.comedogenic_ingredients.length > 0 && (
                              <div className="p-4 bg-amber-50/70 rounded-xl border border-amber-200 space-y-2">
                                <div className="flex items-center justify-between">
                                  <span className="font-bold text-amber-900 text-xs flex items-center gap-1.5">
                                    <span>⚠️</span>
                                    <span>Comedogenic / Pore-Clogging Agents ({telemetry.clinical_dermatology.comedogenic_ingredients.length})</span>
                                  </span>
                                  <span className="text-[10px] text-amber-700 font-semibold">Scale: 1 (Low) – 5 (High)</span>
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                                  {telemetry.clinical_dermatology.comedogenic_ingredients.map((item, idx) => {
                                    const name = typeof item === 'string' ? item : (item.canonical_inci || item.ingredient || item.common_name);
                                    const common = typeof item === 'object' && item.common_name && item.common_name !== name ? item.common_name : null;
                                    const score = typeof item === 'object' && item.comedogenicity ? item.comedogenicity : null;
                                    const note = typeof item === 'object' ? (item.notes || item.evidence) : null;
                                    return (
                                      <div key={idx} className="p-2.5 bg-white rounded-lg border border-amber-200/90 shadow-2xs space-y-1">
                                        <div className="flex items-start justify-between gap-2">
                                          <div>
                                            <span className="font-extrabold text-xs text-amber-950 capitalize">{name}</span>
                                            {common && <span className="text-[11px] text-gray-500 block">{common}</span>}
                                          </div>
                                          {score !== null && (
                                            <span className={`px-2 py-0.5 rounded text-[10px] font-black shrink-0 ${
                                              score >= 4 ? 'bg-rose-100 text-rose-800' : score >= 3 ? 'bg-amber-100 text-amber-800' : 'bg-yellow-100 text-yellow-800'
                                            }`}>
                                              Rating: {score}/5
                                            </span>
                                          )}
                                        </div>
                                        {note && (
                                          <p className="text-[10px] text-gray-600 italic leading-snug line-clamp-2" title={note}>
                                            {note}
                                          </p>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}

                            {/* Irritating Ingredients */}
                            {telemetry.clinical_dermatology.irritating_ingredients && telemetry.clinical_dermatology.irritating_ingredients.length > 0 && (
                              <div className="p-4 bg-rose-50/70 rounded-xl border border-rose-200 space-y-2">
                                <div className="flex items-center justify-between">
                                  <span className="font-bold text-rose-900 text-xs flex items-center gap-1.5">
                                    <span>🛑</span>
                                    <span>Sensitizing & Irritating Ingredients ({telemetry.clinical_dermatology.irritating_ingredients.length})</span>
                                  </span>
                                  <span className="text-[10px] text-rose-700 font-semibold">Scale: 1 (Mild) – 5 (Severe)</span>
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                                  {telemetry.clinical_dermatology.irritating_ingredients.map((item, idx) => {
                                    const name = typeof item === 'string' ? item : (item.canonical_inci || item.ingredient || item.common_name);
                                    const common = typeof item === 'object' && item.common_name && item.common_name !== name ? item.common_name : null;
                                    const score = typeof item === 'object' && item.irritancy ? item.irritancy : null;
                                    const note = typeof item === 'object' ? (item.notes || item.evidence) : null;
                                    return (
                                      <div key={idx} className="p-2.5 bg-white rounded-lg border border-rose-200/90 shadow-2xs space-y-1">
                                        <div className="flex items-start justify-between gap-2">
                                          <div>
                                            <span className="font-extrabold text-xs text-rose-950 capitalize">{name}</span>
                                            {common && <span className="text-[11px] text-gray-500 block">{common}</span>}
                                          </div>
                                          {score !== null && (
                                            <span className={`px-2 py-0.5 rounded text-[10px] font-black shrink-0 ${
                                              score >= 4 ? 'bg-rose-100 text-rose-800' : 'bg-amber-100 text-amber-800'
                                            }`}>
                                              Irritancy: {score}/5
                                            </span>
                                          )}
                                        </div>
                                        {note && (
                                          <p className="text-[10px] text-gray-600 italic leading-snug line-clamp-2" title={note}>
                                            {note}
                                          </p>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}

                            {/* Clean Formulation Notice */}
                            {(!telemetry.clinical_dermatology.comedogenic_ingredients || telemetry.clinical_dermatology.comedogenic_ingredients.length === 0) &&
                             (!telemetry.clinical_dermatology.irritating_ingredients || telemetry.clinical_dermatology.irritating_ingredients.length === 0) && (
                              <div className="p-4 bg-emerald-50 text-emerald-800 rounded-xl border border-emerald-200 font-semibold flex items-center gap-2">
                                <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                                <span>Clean formulation: No severe clinical irritants or high pore-clogging agents flagged.</span>
                              </div>
                            )}
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
                      <div className="flex items-center justify-between pb-1">
                        <div className="font-bold text-gray-700 uppercase text-[10px] tracking-wider">
                          Top Catalog Dupes & Alternatives Breakdown
                        </div>
                        <div className="text-[11px] text-gray-400 font-medium">
                          Click any card to inspect product forensics
                        </div>
                      </div>

                      {telemetry.similar_formulation_matches.length === 0 ? (
                        <div className="p-8 text-center text-gray-400 bg-gray-50 rounded-2xl border">
                          No formulation alternatives matched for this product.
                        </div>
                      ) : (
                        telemetry.similar_formulation_matches.map((alt, idx) => (
                          <div 
                            key={idx}
                            onClick={() => handleOpenTelemetry(alt.id)}
                            className="p-4 rounded-2xl border border-gray-200 bg-white hover:border-purple-400 hover:shadow-md transition-all space-y-3 cursor-pointer group"
                          >
                            <div className="flex items-start justify-between gap-4">
                              <div className="flex items-center gap-3">
                                <div className="w-12 h-12 rounded-xl bg-gray-50 border border-gray-200 flex items-center justify-center shrink-0 overflow-hidden group-hover:border-purple-300">
                                  {alt.image_url ? (
                                    <img src={alt.image_url} alt="" className="max-h-full max-w-full object-contain" onError={(e) => { e.target.style.display = 'none'; }} />
                                  ) : (
                                    <span className="text-lg">🧴</span>
                                  )}
                                </div>
                                <div>
                                  <div className="text-[10px] font-bold text-purple-600 uppercase flex items-center gap-1.5">
                                    <span>{alt.brand}</span>
                                    <span className="text-gray-300">•</span>
                                    <span className="text-gray-400 font-mono">#{alt.id}</span>
                                  </div>
                                  <h4 className="font-bold text-sm text-gray-900 leading-snug group-hover:text-purple-700 transition">
                                    {alt.title}
                                  </h4>
                                  <div className="text-[11px] text-gray-500 mt-0.5">{alt.price ? `${alt.price} EGP` : 'Price N/A'}</div>
                                </div>
                              </div>

                              <div className="flex items-center gap-3 shrink-0">
                                <div className="text-right">
                                  <span className="bg-gradient-to-r from-purple-600 to-pink-600 text-white font-black text-xs px-3 py-1 rounded-full shadow-sm">
                                    {Math.round(alt.match_score)}% Match
                                  </span>
                                  {alt.cross_encoder_score && (
                                    <div className="text-[10px] text-gray-500 mt-1 font-semibold">
                                      Cross-Encoder: {Math.round(alt.cross_encoder_score * 100)}%
                                    </div>
                                  )}
                                </div>
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleOpenTelemetry(alt.id);
                                  }}
                                  className="px-3 py-1.5 bg-purple-50 group-hover:bg-purple-600 text-purple-700 group-hover:text-white font-bold rounded-xl text-xs transition flex items-center gap-1 shrink-0"
                                >
                                  <span>Inspect</span>
                                  <ChevronRight className="w-3.5 h-3.5" />
                                </button>
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
