"""Live-web catalog discoverability tests (buyer transactability loop).

The gateway session searches the MATERIALIZED products; when extraction produced
thin metadata (title fell back to "Live product", no brand/category), every
search missed and the buyer stopped right after discovery — research only, no
quote/cart/checkout. These tests lock the two fixes: real titles extracted from
page metadata, and the any-token search fallback for sparse catalogs.
"""

from app.adapters.commerce.web_live import extract_offer_from_page
from app.db.models import Product, ProductVariant
from app.domain.contracts import SearchProductsRequest
from app.services.catalog import search_products


def test_heuristic_extraction_captures_og_title():
    html = """
    <html><head>
      <meta property="og:title" content="Ultraboost 5 Shoes" />
    </head><body>
      <h1>Ultraboost 5 Shoes</h1>
      <span>Rs. 12,999</span>
      <button>Add to Bag</button>
    </body></html>
    """
    offer = extract_offer_from_page(
        "https://www.adidas.co.in/ultraboost-5-shoes/ID8812.html", html
    )
    assert offer["price_minor"] == 1_299_900  # Rs 12,999
    assert offer["title"] == "Ultraboost 5 Shoes"
    assert offer["method"] == "heuristic"
    assert offer["available_for_sale"] is True  # "Add to Bag" + no OOS signal


def test_jsonld_title_falls_back_to_meta_when_name_missing():
    html = """
    <html><head>
      <meta property="og:title" content="Adidas Ultraboost 5" />
    </head><body>
      <script type="application/ld+json">
        {"@type":"Product","offers":{"price":"12999","priceCurrency":"INR",
         "availability":"https://schema.org/InStock"}}
      </script>
    </body></html>
    """
    offer = extract_offer_from_page("https://www.adidas.co.in/p/1", html)
    assert offer["method"] == "jsonld"
    assert offer["price_minor"] == 1_299_900
    assert offer["title"] == "Adidas Ultraboost 5"
    assert offer["available_for_sale"] is True


def _seed_live_product(db, merchant_id="m1"):
    product = Product(
        merchant_id=merchant_id,
        external_id="https://www.adidas.co.in/ultraboost-5-shoes/ID8812.html",
        title="Ultraboost 5 Shoes",
        status="active",
        source="live_web",
    )
    db.add(product)
    db.flush()
    db.add(
        ProductVariant(
            product_id=product.id,
            external_id="https://www.adidas.co.in/ultraboost-5-shoes/ID8812.html",
            title="live-web offer",
            price=1_299_900,
            currency="INR",
            available_for_sale=True,
            available_quantity=1,
        )
    )
    db.commit()
    return product


def test_search_full_phrase_still_matches(db_session):
    _seed_live_product(db_session)
    hits = search_products(db_session, "m1", SearchProductsRequest(query="Ultraboost 5 Shoes"))
    assert len(hits) == 1
    assert hits[0].title == "Ultraboost 5 Shoes"


def test_search_token_fallback_finds_sparse_live_web_products(db_session):
    _seed_live_product(db_session)
    # A natural-language brain query misses the full phrase but must still find
    # the product via any-token matching ("shoes") — otherwise the buyer stops
    # right after discovery.
    hits = search_products(db_session, "m1", SearchProductsRequest(query="beginner running shoes"))
    assert len(hits) == 1
    assert hits[0].title == "Ultraboost 5 Shoes"
    # No token matches -> still empty (no false positives).
    assert search_products(db_session, "m1", SearchProductsRequest(query="quantum flux capacitor")) == []