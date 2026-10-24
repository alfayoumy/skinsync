import React, { useState, useEffect, useRef } from 'react';
import { 
  Database, Search, Filter, ShieldCheck, ChevronLeft, ChevronRight, 
  ExternalLink, Eye, X, Loader2, Store, BadgePercent, TrendingDown, 
  Check, AlertTriangle, Activity, Sparkles, CheckCircle2, ArrowRight,
  RefreshCw, SlidersHorizontal, RotateCcw, ArrowUpDown, Tag, DollarSign,
  Layers, Settings2, Plus, Trash2, ListChecks, Zap, Copy, Clock,
  Cpu, Globe, Server, AlertOctagon, CheckCircle
} from 'lucide-react';

const SKINCARE_CATEGORIES = [
  'Cleanser', 'Moisturizer', 'Serum', 'Sunscreen', 'Tanning',
  'Body Care', 'Toner', 'Exfoliator', 'Eye Skincare', 'Mask', 'Lip Care', 'Treatment'
];

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
  const [activeTab, setActiveTab] = useState('scraped'); // 'scraped', 'ingredients', 'inci', 'waterfall', 'pricing', 'dermo', 'similar'
  
  // Waterfall Multi-Tier Comparator States
  const [executingTiers, setExecutingTiers] = useState(false);
  const [applyingTier, setApplyingTier] = useState(null);
  const [includeLangSearch, setIncludeLangSearch] = useState(false);
  const [tierSuccessMsg, setTierSuccessMsg] = useState(null);
  const [copiedTier, setCopiedTier] = useState(null);
  const [langsearchQuota, setLangsearchQuota] = useState(null);

  // 3-API Status & Quotas Modal States (Gemini, Jev, LangSearch)
  const [showApiStatusModal, setShowApiStatusModal] = useState(false);
  const [apiStatusData, setApiStatusData] = useState(null);
  const [loadingApiStatus, setLoadingApiStatus] = useState(false);
  const [apiActionLoading, setApiActionLoading] = useState(null);
  const [apiActionMessage, setApiActionMessage] = useState(null);

  // Similar Alternatives Filter States
  const [altCategoryMode, setAltCategoryMode] = useState('same_category'); // 'same_category', 'cross_category', 'specific'
  const [altSelectedCategory, setAltSelectedCategory] = useState('');
  const [loadingAlternatives, setLoadingAlternatives] = useState(false);

  // Whitelist Manager States
  const [showWhitelistModal, setShowWhitelistModal] = useState(false);
  const [whitelistItems, setWhitelistItems] = useState([]);
  const [whitelistTotal, setWhitelistTotal] = useState(0);
  const [whitelistStats, setWhitelistStats] = useState(null);
  const [loadingWhitelist, setLoadingWhitelist] = useState(false);
  const [whitelistQuery, setWhitelistQuery] = useState('');
  const [whitelistCategory, setWhitelistCategory] = useState('');
  const [whitelistPage, setWhitelistPage] = useState(0);
  const [whitelistPageSize, setWhitelistPageSize] = useState(25);
  const [showAddIngForm, setShowAddIngForm] = useState(false);
  const [newIngName, setNewIngName] = useState('');
  const [newIngCanonical, setNewIngCanonical] = useState('');
  const [newIngCategory, setNewIngCategory] = useState('general');
  const [submittingIng, setSubmittingIng] = useState(false);
  const [reconcilingPending, setReconcilingPending] = useState(false);
  const [reconcileResult, setReconcileResult] = useState(null);

  const searchDebounceRef = useRef(null);
  const whitelistDebounceRef = useRef(null);

  const fetchWhitelist = async (pageToFetch = whitelistPage, query = whitelistQuery, cat = whitelistCategory) => {
    try {
      setLoadingWhitelist(true);
      const params = new URLSearchParams();
      params.append('limit', whitelistPageSize.toString());
      params.append('offset', (pageToFetch * whitelistPageSize).toString());
      if (query.trim()) params.append('q', query.trim());
      if (cat) params.append('category', cat);
      const res = await fetch(`http://127.0.0.1:8000/admin/whitelist?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setWhitelistItems(data.items || []);
        setWhitelistTotal(data.total || 0);
        setWhitelistStats(data.stats || null);
      }
    } catch (err) {
      console.error('Failed to fetch whitelist:', err);
    } finally {
      setLoadingWhitelist(false);
    }
  };

  useEffect(() => {
    if (showWhitelistModal) {
      fetchWhitelist(whitelistPage, whitelistQuery, whitelistCategory);
    }
  }, [showWhitelistModal, whitelistPage, whitelistCategory]);

  const handleAddIngredient = async (e) => {
    e.preventDefault();
    if (!newIngName.trim()) return;
    try {
      setSubmittingIng(true);
      const res = await fetch('http://127.0.0.1:8000/admin/whitelist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newIngName.trim(),
          canonical_name: newIngCanonical.trim() || newIngName.trim(),
          category: newIngCategory,
          is_active: true
        })
      });
      if (res.ok) {
        setNewIngName('');
        setNewIngCanonical('');
        setShowAddIngForm(false);
        setActionMessage({ type: 'success', text: `Added "${newIngName.trim()}" to verified whitelist.` });
        fetchWhitelist(0, whitelistQuery, whitelistCategory);
      } else {
        const err = await res.json();
        setActionMessage({ type: 'error', text: err.detail || 'Failed to add ingredient.' });
      }
    } catch (err) {
      setActionMessage({ type: 'error', text: 'Network error adding ingredient.' });
    } finally {
      setSubmittingIng(false);
    }
  };

  const handleToggleActive = async (item) => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/admin/whitelist/${item.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !item.is_active })
      });
      if (res.ok) {
        fetchWhitelist(whitelistPage, whitelistQuery, whitelistCategory);
      }
    } catch (err) {
      console.error('Failed to toggle ingredient active status:', err);
    }
  };

  const handleDeleteIngredient = async (item) => {
    if (!window.confirm(`Delete "${item.name}" from whitelist?`)) return;
    try {
      const res = await fetch(`http://127.0.0.1:8000/admin/whitelist/${item.id}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        setActionMessage({ type: 'success', text: `Deleted "${item.name}" from whitelist.` });
        fetchWhitelist(whitelistPage, whitelistQuery, whitelistCategory);
      }
    } catch (err) {
      console.error('Failed to delete ingredient:', err);
    }
  };

  const handleReconcilePending = async () => {
    try {
      setReconcilingPending(true);
      setReconcileResult(null);
      const res = await fetch('http://127.0.0.1:8000/admin/whitelist/reconcile-pending', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: 250, min_ingredients: 1 })
      });
      if (res.ok) {
        const data = await res.json();
        setReconcileResult(data);
        setActionMessage({
          type: 'success',
          text: `Whitelist Fallback Complete: Extracted verified ingredients for ${data.extracted} products (${data.still_empty} still empty)!`
        });
        fetchProducts(page, searchQuery);
        fetchOverview();
      } else {
        setActionMessage({ type: 'error', text: 'Failed to run whitelist fallback.' });
      }
    } catch (err) {
      setActionMessage({ type: 'error', text: 'Network error running whitelist fallback.' });
    } finally {
      setReconcilingPending(false);
    }
  };

  // Load Overview Data & Brands
  useEffect(() => {
    fetchOverview();
    fetchBrands();
    fetchAllApisStatus();
  }, []);

  const fetchOverview = async () => {
    try {
      setLoadingOverview(true);
      const res = await fetch('http://127.0.0.1:8000/admin/overview');
      if (res.ok) {
        const data = await res.json();
        setOverview(data);
        if (data.langsearch_quota) {
          setLangsearchQuota(data.langsearch_quota);
        }
      }
    } catch (err) {
      console.error('Failed to load admin overview:', err);
    } finally {
      setLoadingOverview(false);
    }
  };

  const fetchLangSearchQuota = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/admin/langsearch/quota-status');
      if (res.ok) {
        const data = await res.json();
        setLangsearchQuota(data);
      }
    } catch (err) {
      console.error('Failed to fetch LangSearch quota:', err);
    }
  };

  const fetchAllApisStatus = async () => {
    try {
      setLoadingApiStatus(true);
      const res = await fetch('http://127.0.0.1:8000/admin/apis/status');
      if (res.ok) {
        const data = await res.json();
        setApiStatusData(data);
        if (data.langsearch) {
          setLangsearchQuota(data.langsearch);
        }
      }
    } catch (err) {
      console.error('Failed to fetch APIs status:', err);
    } finally {
      setLoadingApiStatus(false);
    }
  };

  const handleResetLangsearchQuota = async () => {
    try {
      setApiActionLoading('reset_ls');
      const res = await fetch('http://127.0.0.1:8000/admin/langsearch/reset-quota', { method: 'POST' });
      if (res.ok) {
        await fetchAllApisStatus();
        setApiActionMessage({ type: 'success', text: 'LangSearch quota counter reset to 0. All 10M daily tokens restored!' });
        setTimeout(() => setApiActionMessage(null), 4000);
      }
    } catch (err) {
      console.error('Failed to reset LangSearch quota:', err);
      alert('Failed to reset LangSearch quota.');
    } finally {
      setApiActionLoading(null);
    }
  };

  const handleSetLangsearchExhausted = async () => {
    try {
      setApiActionLoading('exhaust_ls');
      const res = await fetch('http://127.0.0.1:8000/admin/langsearch/set-quota-exhausted', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tokens_used: 10000000, reason: 'Manual Admin 100% allowance simulation' })
      });
      if (res.ok) {
        await fetchAllApisStatus();
        setApiActionMessage({ type: 'warning', text: 'LangSearch marked as 100% daily allowance exhausted until 00:00 UTC.' });
        setTimeout(() => setApiActionMessage(null), 4000);
      }
    } catch (err) {
      console.error('Failed to set quota exhausted:', err);
    } finally {
      setApiActionLoading(null);
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
  const handleOpenTelemetry = async (productId, initialTab = 'scraped') => {
    setSelectedProductId(productId);
    setActiveTab(initialTab);
    setAltCategoryMode('same_category');
    setAltSelectedCategory('');
    setLoadingTelemetry(true);
    setTelemetry(null);
    setTierSuccessMsg(null);
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

  // Execute all 7 tiers for the current product
  const handleExecuteAllTiers = async () => {
    if (!selectedProductId) return;
    try {
      setExecutingTiers(true);
      setTierSuccessMsg(null);
      const res = await fetch(`http://127.0.0.1:8000/admin/products/${selectedProductId}/execute-all-tiers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ langsearch_enabled: includeLangSearch })
      });
      if (res.ok) {
        const data = await res.json();
        setTelemetry(prev => prev ? { ...prev, tier_resolutions: data } : prev);
        setTierSuccessMsg(`All 7 tiers evaluated successfully! ${data?.summary?.resolved_count || 0} tiers resolved.`);
        setTimeout(() => setTierSuccessMsg(null), 5000);
        fetchLangSearchQuota();
      } else {
        alert('Failed to execute waterfall tiers.');
      }
    } catch (err) {
      console.error('Error executing waterfall tiers:', err);
      alert('Error executing waterfall tiers.');
    } finally {
      setExecutingTiers(false);
    }
  };

  // Apply a specific tier resolution as the product's active formula
  const handleApplyTier = async (tierCode) => {
    if (!selectedProductId) return;
    try {
      setApplyingTier(tierCode);
      setTierSuccessMsg(null);
      const res = await fetch(`http://127.0.0.1:8000/admin/products/${selectedProductId}/apply-tier`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier_code: tierCode })
      });
      if (res.ok) {
        const data = await res.json();
        // Re-fetch telemetry to reflect new active formula across all tabs
        const telRes = await fetch(`http://127.0.0.1:8000/admin/products/${selectedProductId}/telemetry`);
        if (telRes.ok) {
          const updatedTelemetry = await telRes.json();
          setTelemetry(updatedTelemetry);
        }
        // Also update products list in background so table row reflects new source
        fetchProducts();
        setTierSuccessMsg(`Successfully applied ${tierCode.toUpperCase()} as active catalog formula!`);
        setTimeout(() => setTierSuccessMsg(null), 5000);
      } else {
        const errData = await res.json().catch(() => ({}));
        alert(errData.detail || 'Failed to apply tier outcome.');
      }
    } catch (err) {
      console.error('Error applying tier outcome:', err);
      alert('Network error while applying tier.');
    } finally {
      setApplyingTier(null);
    }
  };

  // Fetch updated alternatives dynamically based on category mode filter
  const fetchAlternatives = async (productId, mode, specificCat = '') => {
    if (!productId) return;
    try {
      setLoadingAlternatives(true);
      const params = new URLSearchParams();
      params.append('category_mode', mode);
      if (mode === 'specific' && specificCat) {
        params.append('target_category', specificCat);
      }
      const res = await fetch(`http://127.0.0.1:8000/admin/products/${productId}/alternatives?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setTelemetry(prev => prev ? {
          ...prev,
          similar_formulation_matches: data.results || []
        } : prev);
      }
    } catch (err) {
      console.error('Failed to fetch filtered alternatives:', err);
    } finally {
      setLoadingAlternatives(false);
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
              onClick={() => {
                fetchAllApisStatus();
                setShowApiStatusModal(true);
              }}
              className={`text-xs font-bold px-3 py-2 rounded-xl border transition-all flex items-center gap-1.5 cursor-pointer shadow-sm ${
                apiStatusData?.summary?.has_issues
                  ? 'border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100'
                  : 'border-blue-200 bg-blue-50 text-blue-800 hover:bg-blue-100'
              }`}
              title="Inspect Real-time Status, Rate Limits, and Quotas for Gemini, Jev, and LangSearch APIs"
            >
              <Cpu className="w-3.5 h-3.5 text-blue-600" />
              <span>3-API Status & Quotas</span>
              {apiStatusData?.summary?.has_issues && (
                <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse ml-0.5" />
              )}
            </button>

            <button
              onClick={() => setShowWhitelistModal(true)}
              className="text-xs font-bold px-3 py-2 rounded-xl border border-teal-200 bg-teal-50 text-teal-800 hover:bg-teal-100 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
              title="Inspect & Curate 19,000+ Verified Ingredients in SQLite Whitelist"
            >
              <Sparkles className="w-3.5 h-3.5 text-teal-600" />
              <span>Whitelist Manager</span>
            </button>

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
                  <option value="skinsafe_matched">SkinSAFE Matched</option>
                  <option value="whitelist_extracted">Whitelist Description Lookup</option>
                  <option value="cross_store_linked">Cross-Store Clustered</option>
                  <option value="v10_curated">Curated DB</option>
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
                          p.ingredient_source === 'whitelist_extracted' ? (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleOpenTelemetry(p.id, 'waterfall');
                              }}
                              className="bg-teal-100 hover:bg-teal-200 text-teal-800 font-bold px-2 py-0.5 rounded-full text-[10px] flex items-center gap-1 w-fit transition-all cursor-pointer"
                              title="Extracted from store description via verified ingredient whitelist fallback. Click to view & compare all 7 waterfall tiers"
                            >
                              <Sparkles className="w-3 h-3 text-teal-600" /> Whitelist Lookup
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleOpenTelemetry(p.id, 'waterfall');
                              }}
                              className="bg-emerald-100 hover:bg-emerald-200 text-emerald-800 font-bold px-2 py-0.5 rounded-full text-[10px] flex items-center gap-1 w-fit transition-all cursor-pointer"
                              title={`Source: ${p.ingredient_source || 'Available'}. Click to view & compare all 7 waterfall tiers`}
                            >
                              <Check className="w-3 h-3" /> {p.ingredient_source || 'Available'}
                            </button>
                          )
                        ) : (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleOpenTelemetry(p.id, 'waterfall');
                            }}
                            className="bg-gray-100 hover:bg-purple-100 hover:text-purple-700 text-gray-400 font-medium px-2 py-0.5 rounded text-[10px] transition-all cursor-pointer flex items-center gap-1"
                            title="No ingredients found. Click to open 7-tier waterfall comparator and resolve"
                          >
                            <Zap className="w-2.5 h-2.5" /> Missing
                          </button>
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
                onClick={() => setActiveTab('waterfall')}
                className={`py-3 px-4 border-b-2 transition-all cursor-pointer flex items-center gap-1.5 ${activeTab === 'waterfall' ? 'border-purple-600 text-purple-700 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                <Zap className="w-3.5 h-3.5 text-amber-500" />
                <span>⚡ 7-Tier Waterfall</span>
                {telemetry?.tier_resolutions?.summary?.resolved_count > 0 && (
                  <span className="px-1.5 py-0.2 text-[10px] bg-purple-100 text-purple-700 rounded-full font-extrabold">
                    {telemetry.tier_resolutions.summary.resolved_count}/7
                  </span>
                )}
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

                      {/* Ingestion Source URL Link */}
                      {(telemetry.ingredients_forensics.ingredient_source_url || telemetry.product?.ingredient_source_url) && (
                        <div className="p-3 bg-purple-50/70 rounded-xl border border-purple-200 flex items-center justify-between gap-3 text-xs">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-base shrink-0">
                              {(telemetry.ingredients_forensics.ingredient_source_url || telemetry.product?.ingredient_source_url).includes('inkeedecoder.com') || (telemetry.ingredients_forensics.ingredient_source_url || telemetry.product?.ingredient_source_url).includes('incidecoder.com') ? '🌿' : '🔗'}
                            </span>
                            <div className="truncate">
                              <span className="font-bold text-gray-500 uppercase text-[10px] block">Verified Ingestion Source Link</span>
                              <span className="font-semibold text-purple-900 truncate block">
                                {(telemetry.ingredients_forensics.ingredient_source_url || telemetry.product?.ingredient_source_url).includes('inkeedecoder.com') || (telemetry.ingredients_forensics.ingredient_source_url || telemetry.product?.ingredient_source_url).includes('incidecoder.com')
                                  ? 'INKEEDecoder Verified Cosmetic Database'
                                  : (telemetry.ingredients_forensics.ingredient_source_url || telemetry.product?.ingredient_source_url)}
                              </span>
                            </div>
                          </div>
                          <a
                            href={telemetry.ingredients_forensics.ingredient_source_url || telemetry.product?.ingredient_source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="px-3 py-1.5 bg-white hover:bg-purple-600 hover:text-white text-purple-700 rounded-lg border border-purple-300 font-bold text-xs flex items-center gap-1.5 shrink-0 transition-all shadow-xs"
                          >
                            <span>Open Link</span>
                            <ExternalLink className="w-3.5 h-3.5" />
                          </a>
                        </div>
                      )}

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

                  {/* TAB 3.5: 7-TIER WATERFALL RESOLUTION ENGINE */}
                  {activeTab === 'waterfall' && (
                    <div className="space-y-6">
                      {/* Control Panel / Actions Card */}
                      <div className="p-5 bg-gradient-to-br from-white to-purple-50/40 rounded-2xl border border-gray-200 shadow-sm space-y-4">
                        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-gray-200/80">
                          <div>
                            <div className="text-[10px] uppercase font-bold text-purple-600 tracking-wider flex items-center gap-1.5">
                              <Zap className="w-3.5 h-3.5 text-amber-500" />
                              7-Tier INCI Waterfall Engine & Comparator
                            </div>
                            <h3 className="font-extrabold text-base text-gray-900 mt-0.5">
                              Multi-Tier Formulation Comparative Analysis
                            </h3>
                            <p className="text-xs text-gray-500 mt-1 max-w-2xl leading-relaxed">
                              Execute, compare, and audit all 7 waterfall tiers independently. Review the extracted formulas, token counts, and confidence scores across each tier, then elect which resolution to apply as the product's active formula.
                            </p>
                          </div>

                          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 shrink-0">
                            {/* LangSearch Toggle Checkbox */}
                            <label className="flex items-center gap-2 px-3 py-2 bg-white rounded-xl border border-gray-200 select-none text-xs cursor-pointer hover:bg-gray-50 transition-all shadow-2xs">
                              <input
                                type="checkbox"
                                checked={includeLangSearch}
                                onChange={(e) => setIncludeLangSearch(e.target.checked)}
                                className="w-4 h-4 text-purple-600 rounded border-gray-300 focus:ring-purple-500 cursor-pointer"
                              />
                              <span className="font-semibold text-gray-700">
                                Include Tier 4 (Web Search)
                              </span>
                            </label>

                            {/* Execute All Tiers Action Button */}
                            <button
                              onClick={handleExecuteAllTiers}
                              disabled={executingTiers}
                              className={`px-4 py-2.5 rounded-xl font-bold text-xs flex items-center justify-center gap-2 text-white shadow-sm transition-all cursor-pointer ${
                                executingTiers
                                  ? 'bg-purple-400 cursor-not-allowed'
                                  : 'bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-700 hover:to-indigo-700 hover:shadow'
                              }`}
                            >
                              {executingTiers ? (
                                <>
                                  <Loader2 className="w-4 h-4 animate-spin" />
                                  <span>Evaluating 7 Tiers...</span>
                                </>
                              ) : (
                                <>
                                  <Zap className="w-4 h-4 text-amber-300" />
                                  <span>Execute All 7 Tiers</span>
                                </>
                              )}
                            </button>
                          </div>
                        </div>

                        {/* Success / Status Notification Banner */}
                        {tierSuccessMsg && (
                          <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl flex items-center gap-2 text-xs font-semibold text-emerald-800">
                            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                            <span>{tierSuccessMsg}</span>
                          </div>
                        )}

                        {/* Resolution Summary Strip */}
                        {telemetry?.tier_resolutions ? (
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            <div className="p-3 bg-white rounded-xl border border-gray-200">
                              <div className="text-[10px] text-gray-400 font-bold uppercase">Tiers Resolved</div>
                              <div className="text-base font-extrabold text-purple-700 mt-0.5">
                                {telemetry.tier_resolutions.summary?.resolved_count || 0} / 7
                              </div>
                            </div>
                            <div className="p-3 bg-white rounded-xl border border-gray-200">
                              <div className="text-[10px] text-gray-400 font-bold uppercase">Pending LLM Gate</div>
                              <div className="text-base font-extrabold text-amber-600 mt-0.5">
                                {telemetry.tier_resolutions.summary?.pending_llm_count || 0}
                              </div>
                            </div>
                            <div className="p-3 bg-white rounded-xl border border-gray-200">
                              <div className="text-[10px] text-gray-400 font-bold uppercase">Selected / Active Tier</div>
                              <div className="text-base font-extrabold text-gray-900 mt-0.5 uppercase truncate" title={telemetry.tier_resolutions.selected_tier || 'None'}>
                                {telemetry.tier_resolutions.selected_tier || 'Default'}
                              </div>
                            </div>
                            <div className="p-3 bg-white rounded-xl border border-gray-200">
                              <div className="text-[10px] text-gray-400 font-bold uppercase">Last Executed</div>
                              <div className="text-xs font-medium text-gray-600 mt-1 truncate">
                                {telemetry.tier_resolutions.executed_at
                                  ? new Date(telemetry.tier_resolutions.executed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                                  : 'Never'}
                              </div>
                            </div>
                          </div>
                        ) : (
                          <div className="p-4 bg-white rounded-xl border border-dashed border-gray-300 text-center text-gray-500">
                            No full waterfall analysis has been executed yet for this product. Click <span className="font-bold text-purple-700">"Execute All 7 Tiers"</span> above to analyze across all 7 sources simultaneously.
                          </div>
                        )}
                      </div>

                      {/* 7 Tiers Side-by-Side Comparison Cards */}
                      {telemetry?.tier_resolutions?.tiers && (
                        <div className="space-y-4">
                          {[
                            { key: 'tier1', label: 'Tier 1', title: 'Current/Historical Store Page INCI Tab' },
                            { key: 'tier2', label: 'Tier 2', title: 'OpenBeauty Facts API' },
                            { key: 'tier3', label: 'Tier 3', title: 'High SkinSAFE Formulation Match (≥95%)' },
                            { key: 'tier4', label: 'Tier 4', title: 'LangSearch INCI Web Search & Extraction' },
                            { key: 'tier5', label: 'Tier 5', title: 'Store Description Whitelist Fallback' },
                            { key: 'tier6', label: 'Tier 6', title: 'Same-Product Cluster Sister Propagation' },
                            { key: 'tier7', label: 'Tier 7', title: 'Ambiguous SkinSAFE Match (Pending LLM)' },
                          ].map(({ key, label, title }) => {
                            const tierData = telemetry.tier_resolutions.tiers[key];
                            if (!tierData) return null;

                            const isSelected = telemetry.tier_resolutions.selected_tier === key;
                            const isResolved = tierData.status === 'resolved';
                            const isPendingLLM = tierData.status === 'pending_llm';
                            const isSkipped = tierData.status === 'skipped';
                            const isNotFound = tierData.status === 'not_found';
                            const hasIngredients = Boolean(tierData.ingredients_raw && tierData.ingredients_raw.trim());

                            return (
                              <div
                                key={key}
                                className={`p-4 rounded-2xl border transition-all ${
                                  isSelected
                                    ? 'bg-purple-50/60 border-purple-400 ring-2 ring-purple-300/60 shadow-sm'
                                    : isResolved
                                    ? 'bg-white border-emerald-200 hover:border-emerald-300'
                                    : isPendingLLM
                                    ? 'bg-white border-amber-200 hover:border-amber-300'
                                    : 'bg-gray-50/80 border-gray-200 opacity-80'
                                }`}
                              >
                                {/* Header: Title, Status, Confidence, Apply Action */}
                                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-gray-100">
                                  <div className="flex items-center gap-2.5 flex-wrap">
                                    <span className={`px-2 py-0.5 rounded-md text-[10px] font-black uppercase tracking-wider ${
                                      isSelected
                                        ? 'bg-purple-600 text-white'
                                        : isResolved
                                        ? 'bg-emerald-600 text-white'
                                        : isPendingLLM
                                        ? 'bg-amber-500 text-white'
                                        : 'bg-gray-200 text-gray-700'
                                    }`}>
                                      {label}
                                    </span>
                                    <h4 className="font-extrabold text-sm text-gray-900">
                                      {tierData.name || title}
                                    </h4>

                                    {/* Status Badge */}
                                    {isResolved && (
                                      <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded-full font-bold text-[10px]">
                                        <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                                        Resolved
                                      </span>
                                    )}
                                    {isPendingLLM && (
                                      <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-amber-100 text-amber-800 rounded-full font-bold text-[10px]">
                                        <Clock className="w-3 h-3 text-amber-600" />
                                        Pending LLM Gate
                                      </span>
                                    )}
                                    {isSkipped && (
                                      <span className="px-2 py-0.5 bg-gray-200 text-gray-600 rounded-full font-medium text-[10px]">
                                        Skipped
                                      </span>
                                    )}
                                    {isNotFound && (
                                      <span className="px-2 py-0.5 bg-gray-100 text-gray-400 rounded-full font-medium text-[10px]">
                                        Not Found
                                      </span>
                                    )}

                                    {/* INKEEDecoder Priority Badge */}
                                    {key === 'tier4' && (tierData.metadata?.is_inkeedecoder || tierData.metadata?.source_url?.includes('inkeedecoder.com') || tierData.metadata?.source_url?.includes('incidecoder.com')) && (
                                      <span className="px-2 py-0.5 bg-emerald-50 text-emerald-800 border border-emerald-200 rounded-full font-bold text-[10px] flex items-center gap-1">
                                        🌿 INKEEDecoder Priority
                                      </span>
                                    )}

                                    {/* Confidence Pill */}
                                    {tierData.confidence > 0 && (
                                      <span className="px-2 py-0.5 bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-full font-semibold text-[10px]">
                                        {Math.round(tierData.confidence * 100)}% Confidence
                                      </span>
                                    )}
                                  </div>

                                  <div className="flex items-center gap-2 shrink-0">
                                    {isSelected ? (
                                      <span className="inline-flex items-center gap-1.5 px-3 py-1 bg-purple-600 text-white rounded-xl font-bold text-xs shadow-sm">
                                        <Check className="w-3.5 h-3.5" />
                                        Active Formula
                                      </span>
                                    ) : hasIngredients ? (
                                      <button
                                        onClick={() => handleApplyTier(key)}
                                        disabled={applyingTier !== null}
                                        className="px-3 py-1.5 bg-white border border-purple-300 text-purple-700 hover:bg-purple-600 hover:text-white rounded-xl font-bold text-xs transition-all shadow-sm flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                                      >
                                        {applyingTier === key ? (
                                          <>
                                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                            <span>Applying...</span>
                                          </>
                                        ) : (
                                          <>
                                            <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                                            <span>Use & Apply This Tier</span>
                                          </>
                                        )}
                                      </button>
                                    ) : null}
                                  </div>
                                </div>

                                {/* Details / Rationale */}
                                <div className="mt-2 text-xs text-gray-600 leading-relaxed">
                                  {tierData.details}
                                </div>

                                {/* Metadata specifics */}
                                {tierData.metadata && Object.keys(tierData.metadata).length > 0 && (
                                  <div className="mt-2 flex flex-wrap gap-2 text-[10px]">
                                    {tierData.metadata.skinsafe_title && (
                                      <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded border border-blue-100">
                                        SkinSAFE Target: <strong className="font-semibold">{tierData.metadata.skinsafe_title}</strong> (Score: {Math.round((tierData.metadata.hybrid_score || 0) * 100)}%)
                                      </span>
                                    )}
                                    {tierData.metadata.sister_title && (
                                      <span className="px-2 py-0.5 bg-purple-50 text-purple-700 rounded border border-purple-100">
                                        Sister Product #{tierData.metadata.sister_product_id}: <strong className="font-semibold">{tierData.metadata.sister_title}</strong>
                                      </span>
                                    )}
                                    {tierData.metadata.source_url && (
                                      <a
                                        href={tierData.metadata.source_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className={`px-2 py-0.5 rounded border flex items-center gap-1 font-semibold ${
                                          tierData.metadata.is_inkeedecoder || tierData.metadata.source_url.includes('inkeedecoder.com') || tierData.metadata.source_url.includes('incidecoder.com')
                                            ? 'bg-emerald-50 text-emerald-800 border-emerald-200 hover:bg-emerald-100'
                                            : 'bg-gray-100 hover:bg-gray-200 text-gray-700 border-gray-200'
                                        }`}
                                      >
                                        {tierData.metadata.is_inkeedecoder || tierData.metadata.source_url.includes('inkeedecoder.com') || tierData.metadata.source_url.includes('incidecoder.com')
                                          ? '🌿 INKEEDecoder Source Link'
                                          : 'Verified Result Link'}
                                        <ExternalLink className="w-2.5 h-2.5" />
                                      </a>
                                    )}
                                  </div>
                                )}

                                {/* If Ingredients Raw Exists: Show Raw, Tokens, Actives */}
                                {hasIngredients && (
                                  <div className="mt-3 pt-3 border-t border-gray-100 space-y-2">
                                    <div className="flex items-center justify-between">
                                      <span className="text-[10px] font-bold uppercase text-gray-500">
                                        Raw Formulation ({tierData.parsed_tokens?.length || 0} ingredients):
                                      </span>
                                      <button
                                        onClick={() => {
                                          navigator.clipboard.writeText(tierData.ingredients_raw);
                                          setCopiedTier(key);
                                          setTimeout(() => setCopiedTier(null), 2000);
                                        }}
                                        className="text-[10px] font-semibold text-purple-600 hover:text-purple-800 flex items-center gap-1 cursor-pointer"
                                      >
                                        <Copy className="w-3 h-3" />
                                        {copiedTier === key ? 'Copied!' : 'Copy Raw'}
                                      </button>
                                    </div>
                                    <div className="p-2.5 bg-white rounded-xl border border-gray-200 font-mono text-[11px] text-gray-800 leading-relaxed max-h-28 overflow-y-auto whitespace-pre-wrap select-all">
                                      {tierData.ingredients_raw}
                                    </div>

                                    {/* Actives & Tokens */}
                                    <div className="flex flex-wrap items-center gap-1.5 pt-1">
                                      {tierData.actives && tierData.actives.length > 0 && (
                                        <div className="flex flex-wrap items-center gap-1 mr-2">
                                          <span className="text-[10px] font-bold text-emerald-700 uppercase mr-1">Actives:</span>
                                          {tierData.actives.map((act, i) => (
                                            <span key={i} className="px-2 py-0.5 bg-emerald-100 text-emerald-800 font-bold rounded text-[10px]">
                                              ✨ {act}
                                            </span>
                                          ))}
                                        </div>
                                      )}

                                      {tierData.parsed_tokens && tierData.parsed_tokens.length > 0 && (
                                        <div className="flex flex-wrap items-center gap-1">
                                          <span className="text-[10px] font-bold text-gray-400 uppercase mr-1">Tokens:</span>
                                          {tierData.parsed_tokens.slice(0, 15).map((tok, i) => (
                                            <span key={i} className="px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px]">
                                              {tok}
                                            </span>
                                          ))}
                                          {tierData.parsed_tokens.length > 15 && (
                                            <span className="text-[10px] text-gray-400 font-medium">
                                              +{tierData.parsed_tokens.length - 15} more
                                            </span>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                )}
                              </div>
                            );
                          })}
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

                      {/* Mode & Category Filter Controls */}
                      <div className="bg-gray-50 border border-gray-200 rounded-2xl p-3 space-y-2.5">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="text-[11px] font-bold text-gray-700 flex items-center gap-1.5">
                            <SlidersHorizontal className="w-3.5 h-3.5 text-purple-600" />
                            <span>Alternative Recommendation Scope:</span>
                            <span className="text-gray-400 font-normal">Switch between same category vs cross-category</span>
                          </div>
                          {loadingAlternatives && (
                            <div className="flex items-center gap-1.5 text-[11px] text-purple-600 font-bold animate-pulse">
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              <span>Reranking alternatives...</span>
                            </div>
                          )}
                        </div>

                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setAltCategoryMode('same_category');
                              setAltSelectedCategory('');
                              fetchAlternatives(telemetry.product.id, 'same_category');
                            }}
                            className={`px-3 py-1.5 rounded-xl font-bold text-xs transition cursor-pointer flex items-center gap-1.5 ${
                              altCategoryMode === 'same_category'
                                ? 'bg-purple-600 text-white shadow-sm'
                                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-100'
                            }`}
                          >
                            <span>🧴 Same Category</span>
                            <span className="opacity-80 font-normal">({telemetry.product.category || 'Standard'})</span>
                          </button>

                          <button
                            type="button"
                            onClick={() => {
                              setAltCategoryMode('cross_category');
                              setAltSelectedCategory('');
                              fetchAlternatives(telemetry.product.id, 'cross_category');
                            }}
                            className={`px-3 py-1.5 rounded-xl font-bold text-xs transition cursor-pointer flex items-center gap-1.5 ${
                              altCategoryMode === 'cross_category'
                                ? 'bg-purple-600 text-white shadow-sm'
                                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-100'
                            }`}
                          >
                            <span>🌐 Cross-Category</span>
                            <span className="opacity-80 font-normal">(All Skincare)</span>
                          </button>

                          <div className="flex items-center gap-1.5 sm:ml-auto">
                            <span className="text-[10px] font-bold text-gray-500 uppercase">Target Category:</span>
                            <select
                              value={altCategoryMode === 'specific' ? altSelectedCategory : ''}
                              onChange={(e) => {
                                const cat = e.target.value;
                                if (cat) {
                                  setAltCategoryMode('specific');
                                  setAltSelectedCategory(cat);
                                  fetchAlternatives(telemetry.product.id, 'specific', cat);
                                } else {
                                  setAltCategoryMode('same_category');
                                  setAltSelectedCategory('');
                                  fetchAlternatives(telemetry.product.id, 'same_category');
                                }
                              }}
                              className="px-2.5 py-1 text-xs border border-gray-300 rounded-lg bg-white text-gray-700 font-medium focus:ring-1 focus:ring-purple-500 focus:outline-none"
                            >
                              <option value="">Specific Category...</option>
                              {SKINCARE_CATEGORIES.map(c => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </select>
                          </div>
                        </div>
                      </div>

                      {/* Alternatives List */}
                      {loadingAlternatives ? (
                        <div className="p-12 text-center flex flex-col items-center justify-center gap-3 bg-white rounded-2xl border border-gray-100 shadow-sm">
                          <Loader2 className="w-7 h-7 animate-spin text-purple-600" />
                          <span className="text-gray-500 font-semibold text-xs">Computing non-water active formulation compatibility...</span>
                        </div>
                      ) : telemetry.similar_formulation_matches.length === 0 ? (
                        <div className="p-8 text-center text-gray-400 bg-gray-50 rounded-2xl border">
                          No formulation alternatives matched for this product with the selected category filter.
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
                                  <div className="text-[10px] font-bold text-purple-600 uppercase flex flex-wrap items-center gap-1.5">
                                    <span>{alt.brand}</span>
                                    <span className="text-gray-300">•</span>
                                    <span className="text-gray-400 font-mono">#{alt.id}</span>
                                    {alt.category && (
                                      <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200">
                                        {alt.category}
                                      </span>
                                    )}
                                    {alt.vehicle && alt.vehicle !== 'general' && (
                                      <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-purple-50 text-purple-700 border border-purple-200">
                                        {alt.vehicle.replace('_', ' ').toUpperCase()}
                                      </span>
                                    )}
                                  </div>
                                  <h4 className="font-bold text-sm text-gray-900 leading-snug group-hover:text-purple-700 transition mt-0.5">
                                    {alt.title}
                                  </h4>
                                  <div className="text-[11px] text-gray-500 mt-0.5 flex items-center gap-2">
                                    <span>{alt.price ? `${alt.price} EGP` : 'Price N/A'}</span>
                                    {alt.store_count > 1 && (
                                      <span className="text-[10px] font-bold text-pink-700 bg-pink-50 border border-pink-200 px-2 py-0.5 rounded-full inline-flex items-center gap-1" title="Available across multiple retailers. Click Inspect to compare prices.">
                                        🏪 {alt.store_count} Stores
                                      </span>
                                    )}
                                  </div>
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
                                  className="px-3 py-1.5 bg-purple-50 group-hover:bg-purple-600 text-purple-700 group-hover:text-white font-bold rounded-xl text-xs transition flex items-center gap-1 shrink-0 cursor-pointer"
                                >
                                  <span>Inspect</span>
                                  <ChevronRight className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            </div>

                            {/* Why it was matched */}
                            <div className="p-3 bg-purple-50/60 rounded-xl border border-purple-100 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-[11px]">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="font-bold text-purple-900">Shared Actives: </span>
                                {alt.shared_actives && alt.shared_actives.length > 0 ? (
                                  alt.shared_actives.map((act) => (
                                    <span key={act} className="inline-flex items-center px-2 py-0.5 rounded-md bg-purple-100 text-purple-800 font-bold text-[10px]">
                                      ✨ {act}
                                    </span>
                                  ))
                                ) : (
                                  <span className="text-purple-700 italic">
                                    {alt.vehicle && alt.vehicle !== 'general' ? `Texture/Vehicle compatibility (${alt.vehicle.replace('_', ' ')})` : 'Base non-water formulation match'}
                                  </span>
                                )}
                              </div>
                              {alt.formula_similarity_percent !== null && (
                                <div className="font-bold text-purple-800 shrink-0">
                                  Non-Water Jaccard: {alt.formula_similarity_percent}%
                                </div>
                              )}
                            </div>

                            {alt.differing_actives && alt.differing_actives.length > 0 && (
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

      {/* 5. Verified Ingredient Whitelist Management Modal */}
      {showWhitelistModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-3xl max-w-5xl w-full max-h-[90vh] shadow-2xl flex flex-col overflow-hidden animate-scale-in border border-gray-100">
            {/* Modal Header */}
            <div className="p-6 border-b border-gray-200 flex items-start justify-between bg-gradient-to-r from-teal-50 via-emerald-50 to-white shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 bg-gradient-to-tr from-teal-600 to-emerald-500 rounded-2xl flex items-center justify-center text-white shadow-md">
                  <Sparkles className="w-6 h-6" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-black text-gray-900">Verified Ingredient Whitelist</h2>
                    <span className="bg-teal-100 text-teal-800 text-xs px-2.5 py-0.5 rounded-full font-bold">
                      {whitelistStats?.total_ingredients ? whitelistStats.total_ingredients.toLocaleString() : '19,716'} Chemicals & Botanicals
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Authoritative SQLite database whitelist used for fallback description extraction when direct INCI lists or SkinSAFE matches are absent.
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowWhitelistModal(false)}
                className="w-8 h-8 rounded-full bg-white hover:bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-400 hover:text-gray-600 transition cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Sub-header Controls Bar */}
            <div className="p-4 bg-gray-50 border-b border-gray-200 flex flex-wrap items-center justify-between gap-3 shrink-0">
              <div className="flex items-center gap-2 flex-1 max-w-md">
                <div className="relative flex-1">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                  <input
                    type="text"
                    value={whitelistQuery}
                    onChange={(e) => {
                      setWhitelistQuery(e.target.value);
                      if (whitelistDebounceRef.current) clearTimeout(whitelistDebounceRef.current);
                      whitelistDebounceRef.current = setTimeout(() => {
                        setWhitelistPage(0);
                        fetchWhitelist(0, e.target.value, whitelistCategory);
                      }, 300);
                    }}
                    placeholder="Search whitelist (e.g. Niacinamide, Centella, Glycerin)..."
                    className="w-full pl-9 pr-3 py-2 bg-white border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-teal-500"
                  />
                </div>

                <select
                  value={whitelistCategory}
                  onChange={(e) => {
                    setWhitelistCategory(e.target.value);
                    setWhitelistPage(0);
                    fetchWhitelist(0, whitelistQuery, e.target.value);
                  }}
                  className="px-3 py-2 bg-white border border-gray-200 rounded-xl text-xs focus:outline-none focus:border-teal-500 font-medium"
                >
                  <option value="">All Categories</option>
                  <option value="active">Active ({whitelistStats?.categories?.active || 0})</option>
                  <option value="botanical">Botanical ({whitelistStats?.categories?.botanical || 0})</option>
                  <option value="emollient">Emollient ({whitelistStats?.categories?.emollient || 0})</option>
                  <option value="humectant">Humectant ({whitelistStats?.categories?.humectant || 0})</option>
                  <option value="preservative">Preservative ({whitelistStats?.categories?.preservative || 0})</option>
                  <option value="surfactant">Surfactant ({whitelistStats?.categories?.surfactant || 0})</option>
                  <option value="general">General ({whitelistStats?.categories?.general || 0})</option>
                </select>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowAddIngForm(!showAddIngForm)}
                  className="px-3 py-2 bg-white hover:bg-gray-100 border border-teal-300 text-teal-800 rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-sm"
                >
                  <Plus className="w-3.5 h-3.5 text-teal-600" />
                  <span>{showAddIngForm ? 'Cancel' : 'Add Ingredient'}</span>
                </button>

                <button
                  onClick={handleReconcilePending}
                  disabled={reconcilingPending}
                  className="px-3 py-2 bg-gradient-to-r from-teal-600 to-emerald-600 hover:from-teal-700 hover:to-emerald-700 text-white rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-sm disabled:opacity-50"
                  title="Run Fallback Whitelist Lookup on Products with No Ingredients"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${reconcilingPending ? 'animate-spin' : ''}`} />
                  <span>{reconcilingPending ? 'Extracting...' : 'Run Fallback on Pending'}</span>
                </button>
              </div>
            </div>

            {/* Add Ingredient Inline Form */}
            {showAddIngForm && (
              <form onSubmit={handleAddIngredient} className="p-4 bg-teal-50/60 border-b border-teal-200 shrink-0 space-y-3">
                <div className="text-xs font-bold text-teal-900 flex items-center gap-1.5">
                  <Plus className="w-3.5 h-3.5 text-teal-700" />
                  <span>Add New Verified Cosmetic Chemical / Botanical</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <label className="text-[10px] font-bold text-gray-500 uppercase">Ingredient Name *</label>
                    <input
                      type="text"
                      required
                      value={newIngName}
                      onChange={(e) => setNewIngName(e.target.value)}
                      placeholder="e.g. Ectoin"
                      className="w-full mt-1 px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs focus:outline-none focus:border-teal-500"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-bold text-gray-500 uppercase">Canonical Name (Optional)</label>
                    <input
                      type="text"
                      value={newIngCanonical}
                      onChange={(e) => setNewIngCanonical(e.target.value)}
                      placeholder="e.g. Ectoin"
                      className="w-full mt-1 px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs focus:outline-none focus:border-teal-500"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-bold text-gray-500 uppercase">Category</label>
                    <select
                      value={newIngCategory}
                      onChange={(e) => setNewIngCategory(e.target.value)}
                      className="w-full mt-1 px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs focus:outline-none focus:border-teal-500"
                    >
                      <option value="active">Active</option>
                      <option value="botanical">Botanical</option>
                      <option value="emollient">Emollient</option>
                      <option value="humectant">Humectant</option>
                      <option value="preservative">Preservative</option>
                      <option value="surfactant">Surfactant</option>
                      <option value="general">General</option>
                    </select>
                  </div>
                </div>
                <div className="flex justify-end gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => setShowAddIngForm(false)}
                    className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-xs font-semibold cursor-pointer"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submittingIng}
                    className="px-4 py-1.5 bg-teal-600 hover:bg-teal-700 text-white rounded-lg text-xs font-bold disabled:opacity-50 cursor-pointer"
                  >
                    {submittingIng ? 'Adding...' : 'Save to Whitelist'}
                  </button>
                </div>
              </form>
            )}

            {/* Whitelist Table Body */}
            <div className="flex-1 overflow-y-auto p-4">
              {loadingWhitelist ? (
                <div className="p-12 text-center text-gray-400 flex flex-col items-center justify-center gap-3">
                  <Loader2 className="w-8 h-8 animate-spin text-teal-600" />
                  <span className="text-xs font-semibold">Loading verified ingredients from database...</span>
                </div>
              ) : whitelistItems.length === 0 ? (
                <div className="p-12 text-center text-gray-400">
                  <p className="text-sm font-semibold">No whitelist ingredients matched your search.</p>
                </div>
              ) : (
                <div className="overflow-x-auto border border-gray-200 rounded-2xl">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-gray-50 border-b border-gray-200 text-[10px] uppercase font-bold text-gray-500">
                      <tr>
                        <th className="py-2.5 px-3">ID</th>
                        <th className="py-2.5 px-3">Verified Ingredient Name</th>
                        <th className="py-2.5 px-3">Canonical Target</th>
                        <th className="py-2.5 px-3">Category</th>
                        <th className="py-2.5 px-3 text-center">Status</th>
                        <th className="py-2.5 px-3 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {whitelistItems.map((item) => (
                        <tr key={item.id} className="hover:bg-gray-50/80 transition-colors">
                          <td className="py-2.5 px-3 font-mono text-gray-400 text-[11px]">{item.id}</td>
                          <td className="py-2.5 px-3 font-bold text-gray-900">{item.name}</td>
                          <td className="py-2.5 px-3 text-gray-600">{item.canonical_name || item.name}</td>
                          <td className="py-2.5 px-3">
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                              item.category === 'active' ? 'bg-purple-100 text-purple-800' :
                              item.category === 'botanical' ? 'bg-emerald-100 text-emerald-800' :
                              item.category === 'emollient' ? 'bg-amber-100 text-amber-800' :
                              item.category === 'humectant' ? 'bg-cyan-100 text-cyan-800' :
                              item.category === 'preservative' ? 'bg-rose-100 text-rose-800' :
                              item.category === 'surfactant' ? 'bg-indigo-100 text-indigo-800' :
                              'bg-gray-100 text-gray-700'
                            }`}>
                              {item.category}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-center">
                            <button
                              onClick={() => handleToggleActive(item)}
                              className={`px-2 py-0.5 rounded-full text-[10px] font-bold cursor-pointer transition ${
                                item.is_active ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-gray-100 text-gray-400'
                              }`}
                            >
                              {item.is_active ? 'Active' : 'Disabled'}
                            </button>
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <button
                              onClick={() => handleDeleteIngredient(item)}
                              className="text-gray-400 hover:text-red-600 p-1 rounded transition cursor-pointer"
                              title="Delete from whitelist"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Whitelist Modal Pagination Footer */}
            <div className="p-4 bg-gray-50 border-t border-gray-200 flex items-center justify-between shrink-0 text-xs">
              <span className="text-gray-500 font-medium">
                Showing {whitelistItems.length} of {whitelistTotal.toLocaleString()} ingredients (Page {whitelistPage + 1} of {Math.ceil(whitelistTotal / whitelistPageSize) || 1})
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    const prev = Math.max(0, whitelistPage - 1);
                    setWhitelistPage(prev);
                    fetchWhitelist(prev, whitelistQuery, whitelistCategory);
                  }}
                  disabled={whitelistPage === 0 || loadingWhitelist}
                  className="px-3 py-1.5 bg-white border border-gray-200 text-gray-700 font-bold rounded-xl disabled:opacity-40 hover:bg-gray-100 cursor-pointer"
                >
                  Previous
                </button>
                <button
                  onClick={() => {
                    const next = whitelistPage + 1;
                    if (next * whitelistPageSize < whitelistTotal) {
                      setWhitelistPage(next);
                      fetchWhitelist(next, whitelistQuery, whitelistCategory);
                    }
                  }}
                  disabled={(whitelistPage + 1) * whitelistPageSize >= whitelistTotal || loadingWhitelist}
                  className="px-3 py-1.5 bg-white border border-gray-200 text-gray-700 font-bold rounded-xl disabled:opacity-40 hover:bg-gray-100 cursor-pointer"
                >
                  Next
                </button>
                <button
                  onClick={() => setShowWhitelistModal(false)}
                  className="px-4 py-1.5 bg-gray-900 text-white font-bold rounded-xl hover:bg-gray-800 cursor-pointer ml-2"
                >
                  Done
                </button>
              </div>
            </div>

          </div>
        </div>
      )}

      {/* 6. 3-API Status & Quotas Modal (Gemini, Jev, LangSearch) */}
      {showApiStatusModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-3xl max-w-5xl w-full max-h-[92vh] shadow-2xl flex flex-col overflow-hidden animate-scale-in border border-gray-100">
            {/* Modal Header */}
            <div className="p-6 border-b border-gray-200 flex items-start justify-between bg-gradient-to-r from-blue-50 via-indigo-50 to-white shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 bg-gradient-to-tr from-blue-600 to-indigo-600 rounded-2xl flex items-center justify-center text-white shadow-md">
                  <Cpu className="w-6 h-6" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-black text-gray-900">External Services & API Quotas</h2>
                    <span className="bg-blue-100 text-blue-800 text-xs px-2.5 py-0.5 rounded-full font-bold">
                      Gemini · Jev · LangSearch
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Live operational telemetry, rate limits, daily token quotas, and worker queue metrics across all 3 AI & search providers.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={fetchAllApisStatus}
                  disabled={loadingApiStatus}
                  className="px-3 py-1.5 rounded-xl border border-gray-200 bg-white hover:bg-gray-50 text-gray-700 text-xs font-bold flex items-center gap-1.5 cursor-pointer shadow-xs disabled:opacity-50"
                  title="Refresh live status from all 3 APIs"
                >
                  <RefreshCw className={`w-3.5 h-3.5 text-gray-500 ${loadingApiStatus ? 'animate-spin' : ''}`} />
                  <span>Refresh</span>
                </button>
                <button
                  onClick={() => setShowApiStatusModal(false)}
                  className="w-8 h-8 rounded-full bg-white hover:bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-400 hover:text-gray-600 transition cursor-pointer"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="p-6 overflow-y-auto space-y-6 flex-1 bg-gray-50/50">
              {/* Alert Notification if any API has issues */}
              {apiStatusData?.summary?.has_issues && (
                <div className="p-4 bg-amber-50 border border-amber-300 rounded-2xl flex items-start gap-3 text-xs text-amber-950 shadow-xs">
                  <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
                  <div className="space-y-1">
                    <span className="font-extrabold text-amber-900">Attention Required:</span>
                    {apiStatusData.summary.issues.map((iss, i) => (
                      <div key={i} className="leading-relaxed font-medium text-amber-900/90">• {iss}</div>
                    ))}
                  </div>
                </div>
              )}

              {/* Action Message Banner */}
              {apiActionMessage && (
                <div className={`p-3.5 rounded-2xl border text-xs font-semibold flex items-center gap-2 shadow-xs ${
                  apiActionMessage.type === 'success' 
                    ? 'bg-emerald-50 border-emerald-300 text-emerald-800' 
                    : 'bg-amber-50 border-amber-300 text-amber-900'
                }`}>
                  <CheckCircle2 className="w-4 h-4 shrink-0" />
                  <span>{apiActionMessage.text}</span>
                </div>
              )}

              {/* 3 API Cards Grid */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                {/* 1. Google Gemini API Card */}
                <div className="bg-white rounded-2xl border border-gray-200 shadow-xs p-5 flex flex-col justify-between hover:border-purple-300 transition-all">
                  <div className="space-y-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-xl bg-purple-100 text-purple-700 flex items-center justify-center font-bold text-xs">
                          ✨
                        </div>
                        <div>
                          <h3 className="font-black text-sm text-gray-900">Google Gemini</h3>
                          <div className="text-[10px] text-gray-400 font-bold uppercase tracking-wider">Tier 7 Fallback</div>
                        </div>
                      </div>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold flex items-center gap-1 border ${
                        apiStatusData?.gemini?.status === 'operational'
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-red-50 text-red-700 border-red-200'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${apiStatusData?.gemini?.status === 'operational' ? 'bg-emerald-500' : 'bg-red-500'}`} />
                        {apiStatusData?.gemini?.status === 'operational' ? 'Active' : 'Missing Key'}
                      </span>
                    </div>

                    <p className="text-xs text-gray-500 leading-relaxed">
                      {apiStatusData?.gemini?.role || 'Gate 4 Disambiguation & Tier 7 Fallback Validator'}
                    </p>

                    <div className="p-3 bg-gray-50 rounded-xl space-y-2 text-xs border border-gray-100">
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Model:</span>
                        <code className="px-1.5 py-0.5 bg-white border border-gray-200 rounded font-mono text-[10px] text-purple-700 font-bold">
                          {apiStatusData?.gemini?.model || 'gemini-3.5-flash-lite'}
                        </code>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Rate Limits:</span>
                        <span className="font-bold text-gray-800">
                          {apiStatusData?.gemini?.limits?.max_rpm || 15} RPM · {apiStatusData?.gemini?.limits?.max_rpd || 1500} RPD
                        </span>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Batch Size:</span>
                        <span className="font-bold text-gray-800">{apiStatusData?.gemini?.limits?.batch_size || 10} items / call</span>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Pace Delay:</span>
                        <span className="font-bold text-gray-800">{apiStatusData?.gemini?.limits?.pace_delay_seconds || 4.0}s</span>
                      </div>
                    </div>

                    <div className="space-y-1.5 pt-1">
                      <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Queue Metrics</div>
                      <div className="grid grid-cols-3 gap-2">
                        <div className="p-2 bg-purple-50 rounded-xl border border-purple-100 text-center">
                          <div className="text-[10px] text-purple-600 font-bold">Pending</div>
                          <div className="text-sm font-extrabold text-purple-900 mt-0.5">
                            {apiStatusData?.gemini?.queue_metrics?.pending_count || 0}
                          </div>
                        </div>
                        <div className="p-2 bg-emerald-50 rounded-xl border border-emerald-100 text-center">
                          <div className="text-[10px] text-emerald-600 font-bold">Approved</div>
                          <div className="text-sm font-extrabold text-emerald-900 mt-0.5">
                            {apiStatusData?.gemini?.queue_metrics?.approved_count || 0}
                          </div>
                        </div>
                        <div className="p-2 bg-rose-50 rounded-xl border border-rose-100 text-center">
                          <div className="text-[10px] text-rose-600 font-bold">Rejected</div>
                          <div className="text-sm font-extrabold text-rose-900 mt-0.5">
                            {apiStatusData?.gemini?.queue_metrics?.rejected_count || 0}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* 2. Jev 1.13 Semantic Matcher Card */}
                <div className="bg-white rounded-2xl border border-gray-200 shadow-xs p-5 flex flex-col justify-between hover:border-indigo-300 transition-all">
                  <div className="space-y-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-xl bg-indigo-100 text-indigo-700 flex items-center justify-center font-bold text-xs">
                          ⚡
                        </div>
                        <div>
                          <h3 className="font-black text-sm text-gray-900">Jev 1.13 Semantic</h3>
                          <div className="text-[10px] text-gray-400 font-bold uppercase tracking-wider">Tier 7 Primary</div>
                        </div>
                      </div>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold flex items-center gap-1 border ${
                        apiStatusData?.jev?.status === 'operational'
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-amber-50 text-amber-800 border-amber-300'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${apiStatusData?.jev?.status === 'operational' ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                        {apiStatusData?.jev?.status === 'operational' ? 'Active' : 'Rate Limited'}
                      </span>
                    </div>

                    <p className="text-xs text-gray-500 leading-relaxed">
                      {apiStatusData?.jev?.role || 'Gate 4 Primary Semantic Matcher & Tier 7 Pre-Filter'}
                    </p>

                    <div className="p-3 bg-gray-50 rounded-xl space-y-2 text-xs border border-gray-100">
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Model:</span>
                        <code className="px-1.5 py-0.5 bg-white border border-gray-200 rounded font-mono text-[10px] text-indigo-700 font-bold">
                          {apiStatusData?.jev?.model || 'jev-1.13-free'}
                        </code>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Pacing & Interval:</span>
                        <span className="font-bold text-gray-800">
                          {apiStatusData?.jev?.limits?.pace_interval_seconds || 2.0}s ({apiStatusData?.jev?.limits?.max_rps || 0.5} RPS)
                        </span>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">HTTP 429 Backoff:</span>
                        <span className="font-bold text-gray-800">{apiStatusData?.jev?.limits?.backoff_seconds || 10.0}s</span>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Timeout:</span>
                        <span className="font-bold text-gray-800">{apiStatusData?.jev?.limits?.timeout_seconds || 8.0}s</span>
                      </div>
                    </div>

                    <div className="space-y-1.5 pt-1">
                      <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Queue Metrics</div>
                      <div className="grid grid-cols-3 gap-2">
                        <div className="p-2 bg-indigo-50 rounded-xl border border-indigo-100 text-center">
                          <div className="text-[10px] text-indigo-600 font-bold">Pending</div>
                          <div className="text-sm font-extrabold text-indigo-900 mt-0.5">
                            {apiStatusData?.jev?.queue_metrics?.pending_count || 0}
                          </div>
                        </div>
                        <div className="p-2 bg-emerald-50 rounded-xl border border-emerald-100 text-center">
                          <div className="text-[10px] text-emerald-600 font-bold">Approved</div>
                          <div className="text-sm font-extrabold text-emerald-900 mt-0.5">
                            {apiStatusData?.jev?.queue_metrics?.approved_count || 0}
                          </div>
                        </div>
                        <div className="p-2 bg-amber-50 rounded-xl border border-amber-100 text-center">
                          <div className="text-[10px] text-amber-700 font-bold">To Gemini</div>
                          <div className="text-sm font-extrabold text-amber-950 mt-0.5">
                            {apiStatusData?.jev?.queue_metrics?.rate_limited_handoff_count || 0}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* 3. LangSearch Web Search Engine Card */}
                <div className="bg-white rounded-2xl border border-gray-200 shadow-xs p-5 flex flex-col justify-between hover:border-amber-300 transition-all">
                  <div className="space-y-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-xl bg-amber-100 text-amber-800 flex items-center justify-center font-bold text-xs">
                          🌐
                        </div>
                        <div>
                          <h3 className="font-black text-sm text-gray-900">LangSearch</h3>
                          <div className="text-[10px] text-gray-400 font-bold uppercase tracking-wider">Tier 4 Web INCI</div>
                        </div>
                      </div>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold flex items-center gap-1 border ${
                        apiStatusData?.langsearch?.is_daily_exhausted
                          ? 'bg-red-100 text-red-800 border-red-300'
                          : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${apiStatusData?.langsearch?.is_daily_exhausted ? 'bg-red-500' : 'bg-emerald-500'}`} />
                        {apiStatusData?.langsearch?.is_daily_exhausted ? '100% Quota Used' : 'Operational'}
                      </span>
                    </div>

                    <p className="text-xs text-gray-500 leading-relaxed">
                      {apiStatusData?.langsearch?.role || 'Tier 4 Web INCI Retrieval & INKEEDecoder Scraper'}
                    </p>

                    {/* Token Quota Progress Meter */}
                    <div className="p-3 bg-gray-50 rounded-xl space-y-2 text-xs border border-gray-100">
                      <div className="flex justify-between items-center font-bold">
                        <span className="text-[11px] text-gray-700">Daily Token Allowance:</span>
                        <span className={apiStatusData?.langsearch?.is_daily_exhausted ? 'text-red-700' : 'text-gray-900'}>
                          {(apiStatusData?.langsearch?.tokens_used_today || 0).toLocaleString()} / {(apiStatusData?.langsearch?.daily_token_limit || 10000000).toLocaleString()}
                        </span>
                      </div>
                      
                      {/* Bar */}
                      <div className="w-full h-2.5 bg-gray-200 rounded-full overflow-hidden">
                        <div 
                          className={`h-full transition-all duration-500 ${
                            apiStatusData?.langsearch?.is_daily_exhausted 
                              ? 'bg-red-500' 
                              : (apiStatusData?.langsearch?.daily_used_percentage > 80 ? 'bg-amber-500' : 'bg-emerald-500')
                          }`}
                          style={{ width: `${Math.min(100, Math.max(3, apiStatusData?.langsearch?.daily_used_percentage || 0))}%` }}
                        />
                      </div>

                      <div className="flex justify-between items-center text-[11px] text-gray-500 pt-0.5">
                        <span>Used: <strong>{apiStatusData?.langsearch?.daily_used_percentage || 0}%</strong></span>
                        <span className="font-semibold text-purple-900">
                          Resets in {apiStatusData?.langsearch?.resets_in_human || '00:00 UTC'}
                        </span>
                      </div>
                    </div>

                    <div className="p-3 bg-gray-50 rounded-xl space-y-1.5 text-xs border border-gray-100">
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">RPS Hard Cap:</span>
                        <span className="font-bold text-gray-800">{apiStatusData?.langsearch?.limits?.max_rps || 5.0} RPS</span>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">TPM Rolling Cap:</span>
                        <span className="font-bold text-gray-800">1,000,000 TPM</span>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Priority Domain:</span>
                        <span className="font-bold text-emerald-700">inkeedecoder.com</span>
                      </div>
                      <div className="flex justify-between items-center text-gray-600">
                        <span className="text-[11px] font-medium">Blacklisted:</span>
                        <span className="font-bold text-red-600">incibeauty.com (hidden)</span>
                      </div>
                    </div>

                    {/* Quota Management Actions */}
                    <div className="pt-2 flex items-center gap-2">
                      <button
                        onClick={handleResetLangsearchQuota}
                        disabled={apiActionLoading !== null}
                        className="flex-1 py-1.5 px-2 bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200 rounded-xl text-[11px] font-bold transition flex items-center justify-center gap-1 cursor-pointer disabled:opacity-50 shadow-2xs"
                        title="Reset daily token counter back to 0 (Unpause LangSearch)"
                      >
                        <RotateCcw className={`w-3 h-3 ${apiActionLoading === 'reset_ls' ? 'animate-spin' : ''}`} />
                        <span>Reset Quota</span>
                      </button>
                      <button
                        onClick={handleSetLangsearchExhausted}
                        disabled={apiActionLoading !== null}
                        className="py-1.5 px-2 bg-amber-50 hover:bg-amber-100 text-amber-900 border border-amber-200 rounded-xl text-[11px] font-bold transition flex items-center justify-center gap-1 cursor-pointer disabled:opacity-50 shadow-2xs"
                        title="Simulate 100% daily quota exhaustion (Pause LangSearch)"
                      >
                        <AlertTriangle className={`w-3 h-3 ${apiActionLoading === 'exhaust_ls' ? 'animate-spin' : ''}`} />
                        <span>100% Used</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="p-4 bg-white border-t border-gray-200 flex items-center justify-between shrink-0 text-xs text-gray-500">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                <span>Centralized Circuit Breakers active · Auto-resets at 00:00 UTC</span>
              </div>
              <button
                onClick={() => setShowApiStatusModal(false)}
                className="px-4 py-2 bg-gray-900 hover:bg-gray-800 text-white font-bold rounded-xl transition cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default AdminObservatory;
