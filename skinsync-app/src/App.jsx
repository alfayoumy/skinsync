import React, { useState } from 'react';
import { Sparkles, ArrowRight, Search, AlertTriangle, ExternalLink, RotateCcw } from 'lucide-react';

const SkinSync = () => {
  const [step, setStep] = useState(1);
  const [skinType, setSkinType] = useState('');
  const [productSearch, setProductSearch] = useState('');
  const [recommendations, setRecommendations] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [productImages, setProductImages] = useState({});

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

      if (url.includes('sourcebeauty.com')) {
        const match = html.match(/src="([^"]*sourcebeauty\.com\/cdn\/shop\/files\/[^"]+)"/);
        if (match) {
          return match[1].startsWith('//') ? 'https:' + match[1] : match[1];
        }
      } else if (url.includes('feel22.com')) {
        const match = html.match(/data-master="([^"]*feel22\.com\/cdn\/shop\/files\/[^"]+)"/);
        if (match) {
          return match[1].startsWith('//') ? 'https:' + match[1] : match[1];
        }
      }
      return null;
    } catch (e) {
      return null;
    }
  };

  const handleSkinTypeSelect = (type) => {
    setSkinType(type);
    setTimeout(() => setStep(2), 300);
  };

  const handleProductSearch = async () => {
    if (!productSearch.trim()) {
      setError('Please enter a product name');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const response = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'claude-sonnet-4-20250514',
          max_tokens: 1000,
          messages: [{
            role: 'user',
            content: `You are helping with a skincare recommendation API. The user wants to find alternatives for: "${productSearch}" and their skin type is "${skinType}".

Make a POST request to this endpoint with the exact product title:
POST /recommend
Body: {"product_title": "${productSearch}", "user_skin_type": "${skinType}"}

Return ONLY a JSON object with this structure:
{
  "user_skin": "string",
  "results": [
    {
      "title": "string",
      "brand": "string",
      "price": number,
      "match_score": number,
      "warnings": "string or null",
      "url": "string"
    }
  ]
}

If the product is not found, return:
{"error": "Product not found. Please check the spelling and try again."}`
          }]
        })
      });

      const data = await response.json();
      const text = data.content.find(c => c.type === 'text')?.text || '';
      const cleanText = text.replace(/```json|```/g, '').trim();
      const result = JSON.parse(cleanText);

      if (result.error) {
        setError(result.error);
        setLoading(false);
        return;
      }

      setRecommendations(result);

      // Load images for each product
      const images = {};
      for (const product of result.results) {
        if (product.url && (product.url.includes('sourcebeauty.com') || product.url.includes('feel22.com'))) {
          const img = await extractImageFromHTML(product.url);
          if (img) images[product.url] = img;
        }
      }
      setProductImages(images);

      setStep(3);
    } catch (e) {
      setError('Failed to get recommendations. Please try again.');
    }
    setLoading(false);
  };

  const reset = () => {
    setStep(1);
    setSkinType('');
    setProductSearch('');
    setRecommendations(null);
    setError('');
    setProductImages({});
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-purple-50 via-pink-50 to-blue-50">
      {/* Header */}
      <div className="bg-white/80 backdrop-blur-md border-b border-purple-100 sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-purple-500 to-pink-500 rounded-xl flex items-center justify-center">
              <Sparkles className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold bg-gradient-to-r from-purple-600 to-pink-600 bg-clip-text text-transparent">
                SkinSync
              </h1>
              <p className="text-xs text-gray-500">Egyptian Skincare AI</p>
            </div>
          </div>
          {step > 1 && (
            <button onClick={reset} className="flex items-center gap-2 text-sm text-gray-600 hover:text-purple-600 transition">
              <RotateCcw className="w-4 h-4" />
              Start Over
            </button>
          )}
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-12">
        {/* Step Indicator */}
        <div className="flex items-center justify-center gap-4 mb-12">
          {[1, 2, 3].map((s) => (
            <div key={s} className="flex items-center gap-4">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold transition-all ${step >= s ? 'bg-gradient-to-br from-purple-500 to-pink-500 text-white scale-110' : 'bg-gray-200 text-gray-400'
                }`}>
                {s}
              </div>
              {s < 3 && <div className={`w-16 h-1 rounded ${step > s ? 'bg-gradient-to-r from-purple-500 to-pink-500' : 'bg-gray-200'}`} />}
            </div>
          ))}
        </div>

        {/* Step 1: Skin Type Selection */}
        {step === 1 && (
          <div className="text-center animate-fadeIn">
            <h2 className="text-4xl font-bold text-gray-800 mb-4">What's your skin type?</h2>
            <p className="text-gray-600 mb-12">Let's find the perfect products for you</p>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 max-w-5xl mx-auto">
              {skinTypes.map((type) => (
                <button
                  key={type.id}
                  onClick={() => handleSkinTypeSelect(type.id)}
                  className="group relative overflow-hidden bg-white rounded-2xl p-8 shadow-lg hover:shadow-2xl transition-all duration-300 hover:-translate-y-2"
                >
                  <div className={`absolute inset-0 bg-gradient-to-br ${type.gradient} opacity-0 group-hover:opacity-10 transition-opacity`} />
                  <div className="text-6xl mb-4">{type.emoji}</div>
                  <h3 className="text-2xl font-bold text-gray-800 mb-2">{type.label}</h3>
                  <p className="text-sm text-gray-500">{type.desc}</p>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 2: Product Search */}
        {step === 2 && (
          <div className="max-w-2xl mx-auto text-center animate-fadeIn">
            <h2 className="text-4xl font-bold text-gray-800 mb-4">Find Better Alternatives</h2>
            <p className="text-gray-600 mb-8">Enter a product you currently use</p>

            <div className="bg-white rounded-2xl shadow-xl p-8">
              <div className="relative mb-6">
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                <input
                  type="text"
                  value={productSearch}
                  onChange={(e) => setProductSearch(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleProductSearch()}
                  placeholder="e.g., CeraVe Hydrating Cleanser"
                  className="w-full pl-12 pr-4 py-4 border-2 border-gray-200 rounded-xl focus:border-purple-400 focus:outline-none text-lg"
                />
              </div>

              {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 text-red-600 text-sm">
                  {error}
                </div>
              )}

              <button
                onClick={handleProductSearch}
                disabled={loading}
                className="w-full bg-gradient-to-r from-purple-500 to-pink-500 text-white font-bold py-4 rounded-xl hover:shadow-lg transition-all flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {loading ? (
                  <div className="w-6 h-6 border-3 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <>
                    Find Alternatives <ArrowRight className="w-5 h-5" />
                  </>
                )}
              </button>

              <div className="mt-6 p-4 bg-purple-50 rounded-lg">
                <p className="text-sm text-purple-700">
                  <strong>Selected skin type:</strong> {skinTypes.find(t => t.id === skinType)?.label}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Step 3: Results */}
        {step === 3 && recommendations && (
          <div className="animate-fadeIn">
            <div className="text-center mb-12">
              <h2 className="text-4xl font-bold text-gray-800 mb-4">Your Perfect Matches</h2>
              <p className="text-gray-600">Top {recommendations.results.length} alternatives for your {skinType} skin</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
              {recommendations.results.map((product, idx) => (
                <div key={idx} className="bg-white rounded-2xl shadow-xl overflow-hidden hover:shadow-2xl transition-all duration-300 hover:-translate-y-1">
                  {/* Product Image */}
                  <div className="relative h-64 bg-gradient-to-br from-purple-100 to-pink-100 flex items-center justify-center">
                    {productImages[product.url] ? (
                      <img
                        src={productImages[product.url]}
                        alt={product.title}
                        className="w-full h-full object-contain p-4"
                      />
                    ) : (
                      <div className="text-6xl">🧴</div>
                    )}
                    <div className="absolute top-4 right-4 bg-gradient-to-r from-purple-500 to-pink-500 text-white px-4 py-2 rounded-full font-bold text-sm shadow-lg">
                      {Math.round(product.match_score)}% Match
                    </div>
                  </div>

                  {/* Product Info */}
                  <div className="p-6">
                    <h3 className="font-bold text-lg text-gray-800 mb-2 line-clamp-2">{product.title}</h3>
                    <p className="text-purple-600 font-semibold mb-4">{product.brand}</p>

                    <div className="flex items-center justify-between mb-4">
                      <span className="text-2xl font-bold text-gray-800">
                        {product.price > 0 ? `${product.price} EGP` : 'Price N/A'}
                      </span>
                    </div>

                    {product.warnings && (
                      <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 flex items-start gap-2">
                        <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                        <p className="text-sm text-red-700">{product.warnings}</p>
                      </div>
                    )}

                    <a
                      href={product.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="w-full bg-gradient-to-r from-purple-500 to-pink-500 text-white font-bold py-3 rounded-xl hover:shadow-lg transition-all flex items-center justify-center gap-2"
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
      `}</style>
    </div>
  );
};

export default SkinSync;