import React, { useState, useEffect, useRef } from 'react';
import { Sparkles, ArrowRight, Search, AlertTriangle, ExternalLink, RotateCcw, Loader2, X } from 'lucide-react';

const SkinSync = () => {
  // Onboarding & Flow State
  const [step, setStep] = useState(1);
  const [isReturningUser, setIsReturningUser] = useState(false); // Toggle for Step 1
  const [userData, setUserData] = useState({
    name: '',
    email: '',
    gender: '',
    ageGroup: '',
    likes: [],
    dislikes: []
  });

  const [skinType, setSkinType] = useState('');

  // Search States
  const [productSearch, setProductSearch] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [isSearching, setIsSearching] = useState(false);

  // Results States
  const [recommendations, setRecommendations] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [productImages, setProductImages] = useState({});

  // Debounce helper to avoid calling API on every single keystroke
  const searchTimeoutRef = useRef(null);

  const skinTypes = [
    { id: 'oily', label: 'Oily', emoji: '💧', desc: 'Shiny, large pores', gradient: 'from-blue-400 to-cyan-400' },
    { id: 'dry', label: 'Dry', emoji: '🏜️', desc: 'Tight, flaky', gradient: 'from-amber-400 to-orange-400' },
    { id: 'sensitive', label: 'Sensitive', emoji: '🌸', desc: 'Reactive, delicate', gradient: 'from-pink-400 to-rose-400' },
    { id: 'normal', label: 'Normal', emoji: '✨', desc: 'Balanced, clear', gradient: 'from-emerald-400 to-teal-400' }
  ];

  const extractImageFromHTML = async (url) => {
    try {
      const response = await fetch(url);
      const html = await response.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');

      let imageUrl = null;

      if (url.includes('sourcebeauty.com')) {
        // Target: <motion-element class="product__media ..."> <img src="...">
        const img = doc.querySelector('motion-element.product__media img');
        if (img) {
          imageUrl = img.getAttribute('src');
        }
      } else if (url.includes('feel22.com')) {
        // Target: <img data-master="..."> inside the product media container
        // Based on snippet: <div class="t4s-product__media ..."> <img data-master="...">
        const img = doc.querySelector('.t4s-product__media img[data-master]');
        if (img) {
          imageUrl = img.getAttribute('data-master');
        }
      }

      if (imageUrl) {
        // Fix protocol-relative URLs (starts with //)
        if (imageUrl.startsWith('//')) {
          return 'https:' + imageUrl;
        }
        return imageUrl;
      }

      return null;
    } catch (e) {
      console.warn('Failed to extract image from', url, e);
      return null;
    }
  };

  // --- Onboarding Handlers ---
  const updateUserData = (field, value) => {
    setUserData(prev => ({ ...prev, [field]: value }));
  };

  const nextStep = () => setStep(prev => prev + 1);
  const prevStep = () => setStep(prev => Math.max(1, prev - 1));

  const submitOnboarding = async () => {
    setLoading(true);
    try {
      await fetch('http://127.0.0.1:8000/onboarding', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: userData.name,
          email: userData.email,
          gender: userData.gender,
          age_group: userData.ageGroup,
          likes: userData.likes,
          dislikes: userData.dislikes
        })
      });
      setStep(0); // Move to Dashboard
    } catch (err) {
      console.error("Failed to save onboarding", err);
      // Proceed anyway? Or show error? Let's proceed for now.
      setStep(0);
    } finally {
      setLoading(false);
    }
  };

  const handleSkinTypeSelect = (type) => {
    setSkinType(type);
    setTimeout(() => setStep(7), 300);
  };

  const handleReturningUserLogin = async () => {
    if (!userData.email) return;
    setLoading(true);
    try {
      const response = await fetch(`http://127.0.0.1:8000/user?email=${encodeURIComponent(userData.email)}`);
      if (response.ok) {
        const data = await response.json();
        setUserData({
          name: data.name,
          email: data.email,
          gender: data.gender,
          ageGroup: data.age_group,
          likes: Array.isArray(data.likes) ? data.likes : (data.likes ? [data.likes] : []),
          dislikes: Array.isArray(data.dislikes) ? data.dislikes : (data.dislikes ? [data.dislikes] : [])
        });
        setStep(0); // Go to Dashboard
        setIsReturningUser(false); // Ensure Step 1 shows edit fields if revisited
      } else {
        alert("Email not found. Please create a new profile.");
      }
    } catch (err) {
      console.error("Login failed", err);
      alert("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // --- Multi-Select Helpers ---
  const handleMultiSearch = (query, field) => {
    setProductSearch(query);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);

    if (query.length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    searchTimeoutRef.current = setTimeout(async () => {
      try {
        const API_URL = `http://127.0.0.1:8000/search?query=${encodeURIComponent(query)}`;
        const response = await fetch(API_URL);
        if (response.ok) {
          const data = await response.json();
          setSuggestions(data);
          setShowSuggestions(true);
        }
      } catch (err) {
        console.error("Search failed", err);
      }
    }, 300);
  };

  const addMultiItem = (field, item) => {
    if (!userData[field].includes(item)) {
      setUserData(prev => ({
        ...prev,
        [field]: [...prev[field], item]
      }));
    }
    setProductSearch('');
    setSuggestions([]);
    setShowSuggestions(false);
  };

  const removeMultiItem = (field, item) => {
    setUserData(prev => ({
      ...prev,
      [field]: prev[field].filter(i => i !== item)
    }));
  };

  // --- NEW: Autocomplete Logic ---
  const handleInputChange = (e) => {
    const value = e.target.value;
    setProductSearch(value);
    setError('');

    // Clear previous timeout
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);

    if (value.length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    // Set new timeout (Debounce 300ms)
    searchTimeoutRef.current = setTimeout(async () => {
      setIsSearching(true);
      try {
        // Point to your Render Backend Search Endpoint
        const API_URL = `http://127.0.0.1:8000/search?query=${encodeURIComponent(value)}`;
        const response = await fetch(API_URL);
        if (response.ok) {
          const data = await response.json();
          setSuggestions(data);
          setShowSuggestions(true);
        }
      } catch (err) {
        console.error("Autocomplete failed", err);
      } finally {
        setIsSearching(false);
      }
    }, 300);
  };

  const selectSuggestion = (title) => {
    setProductSearch(title);
    setSuggestions([]);
    setShowSuggestions(false);
  };
  // -------------------------------

  const handleProductSearch = async () => {
    if (!productSearch.trim()) {
      setError('Please enter a product name');
      return;
    }

    setLoading(true);
    setError('');
    setShowSuggestions(false); // Hide dropdown if open

    try {
      const API_URL = "http://127.0.0.1:8000/recommend";

      const response = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_title: productSearch,
          user_skin_type: skinType
        })
      });

      if (!response.ok) {
        throw new Error('Product not found');
      }

      const data = await response.json();
      setRecommendations(data);

      // Image scraping (Optional)
      const images = {};

      // Scrape Source Product Image
      if (data.source_product?.url && (data.source_product.url.includes('sourcebeauty.com') || data.source_product.url.includes('feel22.com'))) {
        const img = await extractImageFromHTML(data.source_product.url);
        if (img) images[data.source_product.url] = img;
      }

      for (const product of data.results) {
        if (product.url && (product.url.includes('sourcebeauty.com') || product.url.includes('feel22.com'))) {
          const img = await extractImageFromHTML(product.url);
          if (img) images[product.url] = img;
        }
      }
      setProductImages(images);

      setProductImages(images);

      setStep(8);

    } catch (e) {
      console.error(e);
      setError('Product not found. Please select an item from the suggestions.');
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setStep(1);
    setSkinType('');
    setProductSearch('');
    setRecommendations(null);
    setError('');
    setProductImages({});
    setSuggestions([]);
    setIsReturningUser(false);
    setUserData({
      name: '',
      email: '',
      gender: '',
      ageGroup: '',
      likes: [],
      dislikes: []
    });
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-purple-50 via-pink-50 to-blue-50 font-sans">
      {/* Header */}
      <div className="bg-white/80 backdrop-blur-md border-b border-purple-100 sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-purple-500 to-pink-500 rounded-xl flex items-center justify-center shadow-md">
              <Sparkles className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold bg-gradient-to-r from-purple-600 to-pink-600 bg-clip-text text-transparent">
                SkinSync
              </h1>
              <p className="text-xs text-gray-500 font-medium tracking-wide">EGYPTIAN SKINCARE AI</p>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-12">
        {/* Step Indicator (Simplified for 8 steps) */}
        <div className="flex items-center justify-between mb-12">
          {/* Back Button */}
          <div className="w-24">
            {step > 1 && (
              <button onClick={prevStep} className="flex items-center gap-2 text-gray-600 hover:text-purple-600 font-medium transition">
                <ArrowRight className="w-4 h-4 rotate-180" /> Back
              </button>
            )}
          </div>

          {/* Steps */}
          <div className="flex items-center justify-center gap-2 flex-wrap">
            {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => (
              <div key={s} className="flex items-center">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm transition-all shadow-sm ${step >= s ? 'bg-gradient-to-br from-purple-500 to-pink-500 text-white' : 'bg-gray-200 text-gray-400'
                  }`}>
                  {s}
                </div>
                {s < 8 && <div className={`w-4 h-1 mx-1 rounded ${step > s ? 'bg-gradient-to-r from-purple-500 to-pink-500' : 'bg-gray-200'}`} />}
              </div>
            ))}
          </div>

          {/* Start Over Button */}
          <div className="w-24 flex justify-end">
            {step > 1 && (
              <button onClick={reset} className="flex items-center gap-2 text-sm text-gray-600 hover:text-purple-600 transition font-medium">
                <RotateCcw className="w-4 h-4" />
                Reset
              </button>
            )}
          </div>
        </div>

        {/* STEP 0: DASHBOARD */}
        {step === 0 && (
          <div className="max-w-4xl mx-auto animate-fadeIn">
            <div className="bg-white rounded-3xl shadow-xl overflow-hidden mb-8 border border-purple-100">
              <div className="bg-gradient-to-r from-purple-600 to-pink-600 p-8 text-white">
                <h2 className="text-3xl font-bold mb-2">Welcome, {userData.name || 'Friend'}!</h2>
                <p className="opacity-90">Manage your profile and preferences</p>
              </div>

              <div className="p-8 grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="space-y-4">
                  <h3 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                    <span className="text-2xl">👤</span> Personal Info
                  </h3>
                  <div className="bg-gray-50 p-4 rounded-xl space-y-2">
                    <p><span className="font-semibold text-gray-500">Email:</span> {userData.email}</p>
                    <p><span className="font-semibold text-gray-500">Gender:</span> {userData.gender}</p>
                    <p><span className="font-semibold text-gray-500">Age:</span> {userData.ageGroup}</p>
                  </div>
                  <button
                    onClick={() => setStep(1)}
                    className="w-full py-3 border-2 border-purple-100 text-purple-600 font-bold rounded-xl hover:bg-purple-50 transition-colors"
                  >
                    Edit Profile
                  </button>
                </div>

                <div className="space-y-4">
                  <h3 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                    <span className="text-2xl">🧴</span> Preferences
                  </h3>
                  <div className="bg-gray-50 p-4 rounded-xl space-y-4">
                    <div>
                      <p className="font-semibold text-gray-500 text-sm mb-2">LIKES</p>
                      <div className="flex flex-wrap gap-2">
                        {userData.likes.length > 0 ? (
                          userData.likes.map((item, i) => (
                            <span key={i} className="px-3 py-1 bg-green-100 text-green-700 rounded-full text-xs font-bold">
                              {item}
                            </span>
                          ))
                        ) : <p className="text-gray-400 text-sm">No preferences added</p>}
                      </div>
                    </div>
                    <div>
                      <p className="font-semibold text-gray-500 text-sm mb-2">DISLIKES</p>
                      <div className="flex flex-wrap gap-2">
                        {userData.dislikes.length > 0 ? (
                          userData.dislikes.map((item, i) => (
                            <span key={i} className="px-3 py-1 bg-red-100 text-red-700 rounded-full text-xs font-bold">
                              {item}
                            </span>
                          ))
                        ) : <p className="text-gray-400 text-sm">No dislikes added</p>}
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={() => setStep(4)}
                    className="w-full py-3 border-2 border-purple-100 text-purple-600 font-bold rounded-xl hover:bg-purple-50 transition-colors"
                  >
                    Add Likes/Dislikes
                  </button>
                </div>
              </div>

              <div className="p-8 border-t border-gray-100 bg-gray-50">
                <button
                  onClick={() => setStep(6)}
                  className="w-full py-4 bg-gray-900 text-white font-bold rounded-xl hover:bg-purple-600 transition-colors shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"
                >
                  Find a Product Alternative <ArrowRight className="inline w-5 h-5 ml-2" />
                </button>
              </div>
            </div>

            <div className="text-center">
              <button onClick={reset} className="text-gray-400 hover:text-red-500 text-sm">Log out</button>
            </div>
          </div>
        )}

        {/* STEP 1: Name & Email / Returning User */}
        {step === 1 && (
          <div className="max-w-xl mx-auto animate-fadeIn bg-white p-8 rounded-2xl shadow-xl border border-purple-50">
            {!isReturningUser ? (
              <>
                <h2 className="text-3xl font-bold text-gray-800 mb-6 text-center">Let's get to know you</h2>
                <div className="space-y-4">
                  <div>
                    <label className="block text-gray-700 font-medium mb-2">My name is</label>
                    <input
                      type="text"
                      value={userData.name}
                      onChange={(e) => updateUserData('name', e.target.value)}
                      className="w-full p-4 bg-gray-50 rounded-xl border-2 border-transparent focus:border-purple-500 focus:bg-white transition-all outline-none"
                      placeholder="Your Name"
                    />
                  </div>
                  <div>
                    <label className="block text-gray-700 font-medium mb-2">My email is</label>
                    <input
                      type="email"
                      value={userData.email}
                      onChange={(e) => updateUserData('email', e.target.value)}
                      className="w-full p-4 bg-gray-50 rounded-xl border-2 border-transparent focus:border-purple-500 focus:bg-white transition-all outline-none"
                      placeholder="name@example.com"
                    />
                  </div>
                  <button
                    onClick={nextStep}
                    disabled={!userData.name || !userData.email}
                    className="w-full py-4 mt-4 bg-gray-900 text-white rounded-xl font-bold hover:bg-purple-600 transition-colors disabled:opacity-50"
                  >
                    Continue <ArrowRight className="inline w-4 h-4 ml-2" />
                  </button>

                  <div className="text-center mt-6">
                    <button
                      onClick={() => setIsReturningUser(true)}
                      className="text-purple-600 font-bold hover:underline"
                    >
                      Returning user?
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <>
                <h2 className="text-3xl font-bold text-gray-800 mb-6 text-center">Welcome Back!</h2>
                <div className="space-y-4">
                  <div>
                    <label className="block text-gray-700 font-medium mb-2">My email is</label>
                    <input
                      type="email"
                      value={userData.email}
                      onChange={(e) => updateUserData('email', e.target.value)}
                      className="w-full p-4 bg-gray-50 rounded-xl border-2 border-transparent focus:border-purple-500 focus:bg-white transition-all outline-none"
                      placeholder="name@example.com"
                    />
                  </div>
                  <button
                    onClick={handleReturningUserLogin}
                    disabled={!userData.email || loading}
                    className="w-full py-4 mt-4 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-xl font-bold hover:shadow-xl transition-all disabled:opacity-50 flex items-center justify-center"
                  >
                    {loading ? <Loader2 className="animate-spin" /> : "Sign In"}
                  </button>

                  <div className="text-center mt-6">
                    <button
                      onClick={() => setIsReturningUser(false)}
                      className="text-gray-500 hover:text-purple-600 font-medium"
                    >
                      Create a new profile
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* STEP 2: Gender */}
        {step === 2 && (
          <div className="max-w-xl mx-auto animate-fadeIn text-center">
            <h2 className="text-3xl font-bold text-gray-800 mb-8">How do you identify?</h2>
            <div className="grid grid-cols-2 gap-4">
              {['Female', 'Male', 'Non-binary', 'Prefer not to say'].map(g => (
                <button
                  key={g}
                  onClick={() => { updateUserData('gender', g); nextStep(); }}
                  className="p-6 bg-white rounded-2xl shadow-md hover:shadow-xl hover:bg-purple-50 border border-transparent hover:border-purple-200 transition-all font-bold text-gray-700"
                >
                  {g}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* STEP 3: Age Group */}
        {step === 3 && (
          <div className="max-w-xl mx-auto animate-fadeIn text-center">
            <h2 className="text-3xl font-bold text-gray-800 mb-8">What is your age group?</h2>
            <div className="space-y-3">
              {['Under 18', '18 - 24', '25 - 34', '35 - 44', '45 - 54', '55+'].map(age => (
                <button
                  key={age}
                  onClick={() => { updateUserData('ageGroup', age); nextStep(); }}
                  className="w-full p-5 bg-white rounded-xl shadow-sm hover:shadow-md hover:translate-x-2 transition-all text-left font-bold text-gray-700 border border-gray-100 hover:border-purple-200"
                >
                  {age}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* STEP 4: Likes */}
        {step === 4 && (
          <div className="max-w-xl mx-auto animate-fadeIn bg-white p-8 rounded-2xl shadow-xl border border-purple-50">
            <h2 className="text-3xl font-bold text-gray-800 mb-6 text-center">What products do you love?</h2>
            <p className="text-gray-500 text-center mb-6">Search and add products that worked well for you</p>

            <div className="relative mb-6">
              <input
                type="text"
                value={productSearch} // using shared state, ensure to clear on step change
                onChange={(e) => handleMultiSearch(e.target.value, 'likes')}
                placeholder="Search products..."
                className="w-full p-4 bg-gray-50 rounded-xl border-2 border-transparent focus:border-purple-500 focus:bg-white transition-all outline-none"
              />
              {showSuggestions && suggestions.length > 0 && (
                <div className="absolute w-full bg-white mt-2 rounded-xl shadow-2xl border border-gray-100 overflow-hidden z-[100] text-left max-h-60 overflow-y-auto">
                  {suggestions.map((item, idx) => (
                    <button
                      key={idx}
                      onClick={() => addMultiItem('likes', item)}
                      className="w-full px-6 py-3 hover:bg-purple-50 text-gray-700 hover:text-purple-700 transition-colors border-b border-gray-50 text-left"
                    >
                      {item}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-2 mb-6 min-h-[100px] content-start bg-gray-50 p-4 rounded-xl">
              {userData.likes.length === 0 && <p className="text-gray-400 w-full text-center py-8">Added products will appear here</p>}
              {userData.likes.map((item, idx) => (
                <div key={idx} className="flex items-center gap-2 px-4 py-2 bg-green-100 text-green-800 rounded-full font-bold text-sm">
                  {item}
                  <button onClick={() => removeMultiItem('likes', item)} className="hover:text-red-500">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>

            <button
              onClick={() => { setProductSearch(''); nextStep(); }}
              className="w-full py-4 mt-6 bg-gray-900 text-white rounded-xl font-bold hover:bg-purple-600 transition-colors"
            >
              Next <ArrowRight className="inline w-4 h-4 ml-2" />
            </button>
          </div>
        )}

        {/* STEP 5: Dislikes */}
        {step === 5 && (
          <div className="max-w-xl mx-auto animate-fadeIn bg-white p-8 rounded-2xl shadow-xl border border-purple-50">
            <h2 className="text-3xl font-bold text-gray-800 mb-6 text-center">What products didn't work?</h2>
            <p className="text-gray-500 text-center mb-6">Search and add products that caused issues</p>

            <div className="relative mb-6">
              <input
                type="text"
                value={productSearch}
                onChange={(e) => handleMultiSearch(e.target.value, 'dislikes')}
                placeholder="Search products..."
                className="w-full p-4 bg-gray-50 rounded-xl border-2 border-transparent focus:border-purple-500 focus:bg-white transition-all outline-none"
              />
              {showSuggestions && suggestions.length > 0 && (
                <div className="absolute w-full bg-white mt-2 rounded-xl shadow-2xl border border-gray-100 overflow-hidden z-[100] text-left max-h-60 overflow-y-auto">
                  {suggestions.map((item, idx) => (
                    <button
                      key={idx}
                      onClick={() => addMultiItem('dislikes', item)}
                      className="w-full px-6 py-3 hover:bg-purple-50 text-gray-700 hover:text-purple-700 transition-colors border-b border-gray-50 text-left"
                    >
                      {item}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-2 mb-6 min-h-[100px] content-start bg-gray-50 p-4 rounded-xl">
              {userData.dislikes.length === 0 && <p className="text-gray-400 w-full text-center py-8">Added products will appear here</p>}
              {userData.dislikes.map((item, idx) => (
                <div key={idx} className="flex items-center gap-2 px-4 py-2 bg-red-100 text-red-800 rounded-full font-bold text-sm">
                  {item}
                  <button onClick={() => removeMultiItem('dislikes', item)} className="hover:text-red-500">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>

            <button
              onClick={() => { setProductSearch(''); submitOnboarding(); }}
              disabled={loading}
              className="w-full py-4 mt-6 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-xl font-bold hover:shadow-lg hover:scale-[1.02] transition-all"
            >
              {loading ? <Loader2 className="animate-spin mx-auto" /> : "Complete Profile"}
            </button>
          </div>
        )}

        {/* Step 6: Skin Type Selection (Formerly Step 1) */}
        {step === 6 && (
          <div className="text-center animate-fadeIn">
            <h2 className="text-4xl font-bold text-gray-800 mb-4">What's your skin type?</h2>
            <p className="text-gray-600 mb-12 text-lg">Let's find the perfect products for you</p>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 max-w-5xl mx-auto">
              {skinTypes.map((type) => (
                <button
                  key={type.id}
                  onClick={() => handleSkinTypeSelect(type.id)}
                  className="group relative overflow-hidden bg-white rounded-2xl p-8 shadow-lg hover:shadow-2xl transition-all duration-300 hover:-translate-y-2 border border-purple-50"
                >
                  <div className={`absolute inset-0 bg-gradient-to-br ${type.gradient} opacity-0 group-hover:opacity-10 transition-opacity`} />
                  <div className="text-6xl mb-6 transform group-hover:scale-110 transition-transform">{type.emoji}</div>
                  <h3 className="text-2xl font-bold text-gray-800 mb-2">{type.label}</h3>
                  <p className="text-sm text-gray-500">{type.desc}</p>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 7: Product Search (Formerly Step 2) */}
        {step === 7 && (
          <div className="max-w-2xl mx-auto text-center animate-fadeIn">
            <h2 className="text-4xl font-bold text-gray-800 mb-4">Find Better Alternatives</h2>
            <p className="text-gray-600 mb-8 text-lg">Search for a product you currently use</p>

            <div className="bg-white rounded-2xl shadow-xl p-8 border border-purple-50 relative">
              <div className="relative mb-6">
                <div className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400">
                  {isSearching ? <Loader2 className="w-5 h-5 animate-spin text-purple-500" /> : <Search className="w-5 h-5" />}
                </div>

                <input
                  type="text"
                  value={productSearch}
                  onChange={handleInputChange}
                  onKeyPress={(e) => e.key === 'Enter' && handleProductSearch()}
                  placeholder="e.g., CeraVe, Laroche, Vichy..."
                  className="w-full pl-12 pr-4 py-4 border-2 border-gray-200 rounded-xl focus:border-purple-400 focus:outline-none text-lg transition-colors bg-gray-50 focus:bg-white"
                />

                {/* Autocomplete Dropdown */}
                {showSuggestions && suggestions.length > 0 && (
                  <div className="absolute w-full bg-white mt-2 rounded-xl shadow-2xl border border-gray-100 overflow-hidden z-[100] text-left">
                    {suggestions.map((item, idx) => (
                      <button
                        key={idx}
                        onClick={() => selectSuggestion(item)}
                        className="w-full px-6 py-3 hover:bg-purple-50 text-gray-700 hover:text-purple-700 transition-colors border-b border-gray-50 last:border-0 flex items-center gap-3"
                      >
                        <Search className="w-4 h-4 opacity-50" />
                        {item}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 text-red-600 text-sm flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4" /> {error}
                </div>
              )}

              <button
                onClick={handleProductSearch}
                disabled={loading}
                className="w-full bg-gradient-to-r from-purple-500 to-pink-500 text-white font-bold py-4 rounded-xl hover:shadow-lg hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:hover:scale-100"
              >
                {loading ? (
                  <div className="w-6 h-6 border-3 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <>
                    Find Alternatives <ArrowRight className="w-5 h-5" />
                  </>
                )}
              </button>

              <div className="mt-6 p-4 bg-purple-50 rounded-lg border border-purple-100">
                <p className="text-sm text-purple-700">
                  <strong>Selected skin type:</strong> {skinTypes.find(t => t.id === skinType)?.label}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Step 8: Results (Formerly Step 3) */}
        {step === 8 && recommendations && (
          <div className="animate-fadeIn">
            <div className="text-center mb-12">
              <h2 className="text-4xl font-bold text-gray-800 mb-4">Your Perfect Matches</h2>
              <p className="text-gray-600 text-lg">
                Top {recommendations.results.length} alternatives to <span className="font-bold text-purple-600">{recommendations.source_product.title}</span> for your {skinType} skin
              </p>
            </div>

            {/* Selected Product Display */}
            <div className="flex justify-center mb-16">
              <div className="bg-white rounded-2xl shadow-xl overflow-hidden border-2 border-purple-500 max-w-sm w-full relative transform hover:scale-105 transition-transform duration-300">
                <div className="absolute top-4 left-4 z-10 bg-purple-600 text-white px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wide shadow-md">
                  Your Selection
                </div>

                <div className="relative h-64 bg-gradient-to-br from-purple-50 to-pink-50 flex items-center justify-center p-6">
                  {productImages[recommendations.source_product.url] ? (
                    <img
                      src={productImages[recommendations.source_product.url]}
                      alt={recommendations.source_product.title}
                      className="w-full h-full object-contain mix-blend-multiply"
                    />
                  ) : (
                    <div className="text-6xl">🧴</div>
                  )}
                </div>

                <div className="p-6 text-center">
                  <h3 className="font-bold text-lg text-gray-800 mb-2 leading-tight">
                    {recommendations.source_product.title}
                  </h3>
                  <p className="text-purple-500 font-semibold mb-4 text-sm uppercase tracking-wider">
                    {recommendations.source_product.brand}
                  </p>
                  <span className="text-2xl font-bold text-gray-800 bg-purple-50 px-4 py-2 rounded-lg">
                    {recommendations.source_product.price > 0 ? `${recommendations.source_product.price} EGP` : 'Price N/A'}
                  </span>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
              {recommendations.results.map((product, idx) => (
                <div key={idx} className="bg-white rounded-2xl shadow-xl overflow-hidden hover:shadow-2xl hover:-translate-y-1 transition-all duration-300 border border-gray-100 group">
                  {/* Product Image */}
                  <div className="relative h-64 bg-gradient-to-br from-purple-50 to-pink-50 flex items-center justify-center p-6 group-hover:from-purple-100 group-hover:to-pink-100 transition-colors">
                    {productImages[product.url] ? (
                      <img
                        src={productImages[product.url]}
                        alt={product.title}
                        className="w-full h-full object-contain mix-blend-multiply drop-shadow-sm transition-transform duration-500 group-hover:scale-110"
                      />
                    ) : (
                      <div className="text-6xl animate-bounce-slow">🧴</div>
                    )}
                    <div className="absolute top-4 right-4 bg-gradient-to-r from-purple-500 to-pink-500 text-white px-4 py-1.5 rounded-full font-bold text-sm shadow-lg">
                      {Math.round(product.match_score)}% Match
                    </div>
                  </div>

                  {/* Product Info */}
                  <div className="p-6">
                    <h3 className="font-bold text-lg text-gray-800 mb-2 line-clamp-2 min-h-[3.5rem] leading-tight group-hover:text-purple-600 transition-colors">
                      {product.title}
                    </h3>
                    <p className="text-purple-500 font-semibold mb-4 text-sm uppercase tracking-wider">{product.brand}</p>

                    <div className="flex items-center justify-between mb-4 bg-gray-50 p-3 rounded-lg">
                      <span className="text-xl font-bold text-gray-800">
                        {product.price > 0 ? `${product.price} EGP` : 'Price N/A'}
                      </span>
                    </div>

                    {product.warnings && (
                      <div className="bg-red-50 border border-red-100 rounded-lg p-3 mb-4 flex items-start gap-2 text-xs">
                        <AlertTriangle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
                        <p className="text-red-700 leading-snug">{product.warnings}</p>
                      </div>
                    )}

                    <a
                      href={product.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="w-full bg-gray-900 text-white font-bold py-3.5 rounded-xl hover:bg-gradient-to-r hover:from-purple-600 hover:to-pink-600 hover:shadow-lg transition-all flex items-center justify-center gap-2 group-hover:scale-[1.02] active:scale-[0.98]"
                    >
                      View Product <ExternalLink className="w-4 h-4" />
                    </a>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fadeIn {
          animation: fadeIn 0.6s ease-out;
        }
        .animate-bounce-slow {
          animation: bounce 3s infinite;
        }
      `}</style>
    </div>
  );
};

export default SkinSync;