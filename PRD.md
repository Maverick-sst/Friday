# Agent Commerce Gateway
## Shopify V0 - Product Requirements & Implementation Specification

**Document status:** Implementation-ready PRD  
**Prepared:** 23 August 2026  
**Primary use:** Hand-off to coding/build agent  
**Build target:** Razorpay AI Buildathon - Track 01: AI Growth & Agentic Commerce  
**V0 integration:** Shopify merchant only  
**Payment rail:** Razorpay Test Mode  
**Primary demo:** Autonomous purchase of a Shopify product with deterministic policy enforcement and an auditable transaction trace

---

## 0. Executive Summary

### One-sentence thesis

**Turn a normal Shopify merchant into an AI-native merchant - AI discoverable, AI interactable, and AI transactable - without requiring the merchant to rebuild its human-facing website.**

### What we are building

We are building an **Agent Commerce Gateway** and a **Merchant Agent Layer**.

A Shopify merchant connects its store once. Our platform reads the merchant's authorized commerce data, converts it into a canonical **Merchant Agent Profile**, exposes a small standardized set of machine-readable commerce capabilities, allows the merchant to configure transaction policies, and makes the merchant consumable by autonomous buyer agents.

The buyer agent can then:

1. discover the merchant;
2. search the merchant's products;
3. retrieve authoritative product/variant information;
4. request a live quote;
5. evaluate the merchant and product against user and merchant constraints;
6. create a cart/transaction context;
7. pass the action through a deterministic policy engine;
8. initiate a Razorpay Test Mode payment only when policy permits; and
9. record a complete audit trail explaining what happened and why.

### Core product idea

The web is currently optimized for **humans buying from merchants**.

The future commerce interface is increasingly likely to be:

```text
Human
  -> Buyer Agent
  -> Agent Commerce Interface
  -> Merchant
  -> Payment
```

Instead of forcing agents to act like humans through browser automation, scraping, brittle UIs, stale pages, and manual checkout hand-offs, our system gives merchants an **agent-native interface**.

The V0 does not attempt to solve all agentic commerce. It proves one narrow thesis extremely well:

> A single Shopify merchant can become AI-native in minutes, and an arbitrary buyer agent can safely discover and transact with that merchant through a standardized gateway.

---

# 1. Problem Statement

## 1.1 Current problem

Today, an AI agent attempting to buy something from an arbitrary online merchant often has to behave like a human:

```text
Buyer Agent
   |
   v
Search engine / browser
   |
   v
Human web page
   |
   v
Scraping / browser automation
   |
   v
Product discovery
   |
   v
Login / forms / checkout
   |
   v
Human confirmation or manual completion
```

This creates several problems:

- product information may be stale or poorly structured;
- inventory may be difficult to verify;
- the merchant's policies may be buried in natural-language pages;
- browser automation is fragile;
- the agent may need to solve CAPTCHAs, login flows, or dynamic UI state;
- the agent cannot reliably distinguish authoritative transaction data from inferred text;
- financial actions are dangerous when the model is allowed to reason probabilistically all the way into payment execution;
- merchants must redesign or expose new AI-facing interfaces independently if they want to participate in autonomous commerce.

## 1.2 The merchant-side problem

A merchant already has the data and capabilities needed for commerce, but those capabilities are usually exposed primarily through a human-oriented interface.

The merchant should not have to:

- rebuild its website for AI;
- build a custom agent from scratch;
- understand every emerging AI commerce protocol;
- expose its payment secrets to an LLM;
- manually maintain an AI-specific product catalog.

The product should turn the merchant's existing commerce stack into an agent-native interface.

## 1.3 Proposed solution

Create a **Merchant Agent Layer** that acts as an adaptation/translation layer between the merchant's existing commerce system and autonomous agents.

```text
Existing Merchant
      |
      | Shopify authorization
      v
Shopify Adapter
      |
      v
Canonical Commerce Model
      |
      v
Merchant Agent Profile
      |
      v
Agent Commerce Gateway
      |
      v
Buyer Agent
```

The gateway abstracts away Shopify-specific implementation details.

The buyer agent sees only the canonical merchant interface.

---

# 2. Product Vision

## 2.1 The progression

The platform should make a merchant:

```text
AI Discoverable
      |
      v
AI Understandable
      |
      v
AI Interactable
      |
      v
AI Transactable
```

### AI Discoverable

The buyer agent can identify that the merchant exists and determine what the merchant sells.

### AI Understandable

The buyer agent can understand the merchant's category, products, capabilities, policies, pricing model, shipping/return information, and transaction constraints through structured data.

### AI Interactable

The buyer agent can query product data, inventory, quotes, and transaction capabilities through standardized tools/interfaces rather than scraping the storefront.

### AI Transactable

The buyer agent can proceed from a validated quote to a bounded financial action through deterministic policy controls and a payment provider.

---

# 3. Product Goals

## 3.1 Primary goals

1. Onboard one Shopify merchant through a simple connect flow.
2. Generate a canonical Merchant Agent Profile automatically.
3. Synchronize the merchant's catalog and relevant commerce data.
4. Expose the merchant through a standardized Agent Commerce Gateway.
5. Allow a buyer agent to discover/search/select a product.
6. Obtain a live quote before purchase.
7. Enforce deterministic merchant + buyer transaction policies.
8. Execute a Razorpay Test Mode payment only after successful authorization.
9. Record every important decision and state transition in an audit trail.
10. Demonstrate at least one successful transaction and one deliberately blocked transaction.
11. Make the architecture extensible to additional adapters later without implementing them in V0.

## 3.2 Secondary goals

- Give the merchant visibility into what agents can see/do.
- Let the merchant configure transaction policies without code changes.
- Make the agent interaction protocol explicit and easy to inspect.
- Keep the system modular enough that Shopify is an adapter, not the core domain model.

---

# 4. Non-Goals / Explicitly Out of Scope for V0

Do NOT build these unless required to make the primary demo work:

- WooCommerce integration
- Magento integration
- generic custom-commerce connector
- universal web crawler for arbitrary merchants
- multi-merchant marketplace
- full merchant SaaS/billing system
- settlement/reconciliation platform
- real-money production payments
- credit-card storage
- payment credential storage inside the LLM/agent runtime
- support for every commerce protocol (A2A, AP2, ACP, UCP, x402, etc.)
- generalized browser automation
- vector database / RAG system unless a concrete requirement appears
- Kafka
- microservices
- Kubernetes
- distributed workflow orchestration
- production-scale observability platform
- full refund/cancellation automation in the demo
- generalized category schema for restaurants, hotels, travel, insurance, etc.

### Rule

**One merchant platform. One integration. One commerce domain. One purchase path. One strong failure case.**

---

# 5. Target Users

## 5.1 Merchant

A merchant operating a Shopify store who wants its products and transaction capabilities to become available to AI buyers without rebuilding its website.

Primary merchant needs:

- connect store quickly;
- automatically generate agent profile;
- inspect what the AI sees;
- configure transaction policies;
- publish/enable agent commerce;
- understand successful and blocked transactions.

## 5.2 Buyer agent

An autonomous or semi-autonomous AI agent acting on behalf of an end user.

Examples:

- ChatGPT-connected shopping agent;
- custom LLM agent;
- LangGraph/LangChain agent;
- a developer-built agent with tool calling.

The buyer agent should interact with our standardized gateway, not Shopify-specific APIs.

## 5.3 End user

The human who gives the buyer agent the intent and spending constraints.

Example:

> Find Nike Downshifter 14, size 9, under INR 5,000, with reliable returns, and buy the best option.

---

# 6. Core User Journey

## 6.1 Merchant onboarding

```text
Merchant opens platform
       |
       v
Paste Shopify store URL
       |
       v
Shopify detected
       |
       v
Connect / authorize app
       |
       v
Sync merchant data
       |
       v
Generate Merchant Agent Profile
       |
       v
Merchant reviews profile
       |
       v
Merchant configures policies
       |
       v
Publish / enable agent commerce
```

## 6.2 Buyer journey

```text
User intent
   |
   v
Buyer Agent
   |
   v
Discover merchant(s)
   |
   v
Search products
   |
   v
Select candidate
   |
   v
Get live quote
   |
   v
Check product + merchant + user constraints
   |
   v
Policy Engine
   |
   +------------------------------+
   |                              |
 PASS                           FAIL
   |                              |
   v                              v
Create transaction             BLOCK
   |                            + explanation
   v
Razorpay Test Mode
   |
   v
Verify payment result
   |
   v
Complete / fail
   |
   v
Audit trail
```

---

# 7. The Central Architectural Thesis

The architecture must explicitly separate:

## 7.1 Probabilistic intelligence

The agent/LLM may:

- understand user intent;
- identify relevant products;
- compare products;
- interpret natural-language policies;
- choose tools;
- negotiate/request information;
- make recommendations.

## 7.2 Deterministic execution

The system must deterministically validate:

- merchant identity;
- product/variant identity;
- current price;
- current availability;
- quote expiry;
- user authorization limit;
- merchant auto-purchase limit;
- allowed category/region;
- final payable amount;
- whether human approval is required.

### Core rule

> **The LLM can decide what it wants to attempt. It cannot unilaterally decide that money is allowed to move.**

This separation is one of the primary technical differentiators of the demo.

---

# 8. Product Architecture

```text
                                 +------------------+
                                 |      MERCHANT    |
                                 |  Shopify Store   |
                                 +---------+--------+
                                           |
                                  Shopify OAuth/Auth
                                           |
                                           v
+-------------------------------------------------------------------+
|                         OUR PLATFORM                              |
|                                                                   |
|  +----------------+        +-------------------------------+     |
|  | Merchant       |-------> | Shopify Adapter               |     |
|  | Dashboard      |         +---------------+---------------+     |
|  +----------------+                         |                     |
|                                             v                     |
|                              +----------------------------+       |
|                              | Canonical Commerce Model  |       |
|                              +-------------+--------------+       |
|                                            |                      |
|                                            v                      |
|                              +----------------------------+       |
|                              | Merchant Agent Profile    |       |
|                              +-------------+--------------+       |
|                                            |                      |
|                                            v                      |
|                              +----------------------------+       |
|                              | Agent Commerce Gateway    |       |
|                              +-------------+--------------+       |
|                                            |                      |
|                         +------------------+------------------+   |
|                         |                  |                  |   |
|                         v                  v                  v   |
|                    Discovery          Product            Commerce|
|                    /Search            /Quote             /Checkout|
|                         |                  |                  |   |
|                         +------------------+------------------+   |
|                                            |                      |
|                                            v                      |
|                              +----------------------------+       |
|                              | Deterministic Policy      |       |
|                              | Engine                    |       |
|                              +-------------+--------------+       |
|                                            |                      |
|                                            v                      |
|                              +----------------------------+       |
|                              | Razorpay Adapter           |       |
|                              +-------------+--------------+       |
|                                            |                      |
|                                            v                      |
|                              +----------------------------+       |
|                              | Audit / Event Store        |       |
|                              +----------------------------+       |
+-------------------------------------------------------------------+
                                           ^
                                           |
                                  Tool / API interface
                                           |
                                  +--------+--------+
                                  |   BUYER AGENT   |
                                  | LLM + tool call |
                                  +-----------------+
```

---

# 9. Technology Stack

## 9.1 Frontend - React + Vite

### Why

- fast to build;
- ideal for a demo-oriented dashboard;
- component-based;
- strong ecosystem;
- the team already has experience with React/Vite;
- easy to create a polished merchant onboarding and transaction visualization.

### Responsibilities

- merchant onboarding UI;
- Shopify connection state;
- generated profile viewer/editor;
- policy configuration UI;
- AI buyer simulation UI;
- transaction timeline/audit viewer;
- success/failure visualization.

---

## 9.2 Backend - FastAPI + Python

### Why

- rapid API development;
- excellent Pydantic integration;
- strong fit for AI/agent tooling;
- easy async HTTP integration;
- easy integration with Shopify and Razorpay REST/GraphQL APIs;
- straightforward testability.

### Responsibilities

- merchant onboarding;
- Shopify OAuth/session handling;
- synchronization;
- canonicalization;
- gateway APIs;
- buyer-agent tool execution;
- policy evaluation;
- quote generation;
- transaction state machine;
- Razorpay integration;
- audit logging.

---

## 9.3 Database - PostgreSQL

### Why

The system has strongly structured relational entities and state transitions:

- merchants;
- integrations;
- products;
- variants;
- policies;
- quotes;
- carts;
- transactions;
- transaction events.

A relational database provides:

- constraints;
- foreign keys;
- transactional updates;
- reliable state transitions;
- easy querying for audit and demo dashboards.

No vector database is required for V0.

---

## 9.4 ORM - SQLAlchemy

### Why

- mature relational mapping;
- good fit with FastAPI;
- explicit models;
- migration support through Alembic;
- keeps domain logic separate from storage.

---

## 9.5 Validation - Pydantic

### Why

Pydantic models become the canonical contract between:

- API;
- agent tools;
- Shopify adapter;
- policy engine;
- database serialization;
- frontend API responses.

This is especially important for preventing the agent interface from becoming loosely typed.

---

## 9.6 Cache - Redis

### V0 usage

Redis is optional for the first working slice and should be introduced only where useful:

- short-lived quote caching;
- idempotency keys;
- temporary agent/session state;
- rate limiting.

PostgreSQL remains the source of durable application state.

Do not introduce Redis merely because the architecture diagram looks more impressive.

---

## 9.7 Buyer Agent - Tool-calling LLM

Use a model with reliable structured tool calling.

The model should not have direct access to:

- Shopify credentials;
- Razorpay secrets;
- arbitrary HTTP calls;
- database access.

The agent receives a constrained tool set:

```text
search_products
get_product
get_quote
create_cart
checkout
```

The gateway validates every tool call.

The exact model provider is intentionally decoupled from the core architecture.

---

## 9.8 Shopify - GraphQL Admin API / Shopify app authorization

Shopify's current Admin GraphQL API uses app-scoped access tokens and access scopes; the current AppInstallation object exposes the scopes granted to the installed application. Use the current GraphQL Admin API rather than building a new V0 integration on the legacy REST Admin API. Official Shopify documentation currently exposes the 2026-07 API examples and app-installation/access-scope model.

### Responsibilities

- merchant authorization;
- merchant metadata;
- product/variant sync;
- inventory data;
- live source-of-truth validation;
- live quote/commerce state where supported.

### Important security rule

Shopify access tokens stay server-side.

The buyer agent never receives them.

---

## 9.9 Razorpay - Test Mode

Razorpay remains the payment provider, not the core product.

The platform should wrap Razorpay behind a provider interface:

```text
PaymentProvider
    |
    +-- RazorpayProvider
```

Razorpay's current API docs expose Test Keys/Sandbox setup, REST APIs, Orders, and Payments APIs. Orders can be created with amount/currency and linked to payments; Payments APIs can verify/fetch payment state and capture an authorized payment.

### Important boundary

Our V0 should not store raw customer card credentials.

The payment flow should use Razorpay's supported test-mode mechanism and keep payment credentials completely outside the LLM/tool layer.

---

## 9.10 Docker

Use Docker Compose for local reproducibility:

```text
api
web
postgres
redis (optional)
```

No Kubernetes.

---

# 10. Canonical Merchant Agent Profile

The profile is the foundation of the system.

## 10.1 Design principle

The profile must be **platform-neutral**.

The buyer agent should never need to know that a merchant is implemented in Shopify.

## 10.2 Smallest useful model

```json
{
  "merchant": {
    "id": "velocity-sports-001",
    "name": "Velocity Sports",
    "description": "Online sportswear and running equipment store",
    "category": "sportswear",
    "subcategories": [
      "running_shoes",
      "sportswear"
    ],
    "website": "https://example.com",
    "agent_endpoint": "https://gateway.example/v1/merchants/velocity-sports-001"
  },
  "commerce": {
    "currency": "INR",
    "capabilities": [
      "search_products",
      "get_product",
      "get_quote",
      "create_cart",
      "checkout"
    ]
  },
  "policies": {
    "max_auto_purchase": 5000,
    "requires_human_approval_above": 5000,
    "return_window_days": 7,
    "allow_cancellation": true
  },
  "source": {
    "provider": "shopify",
    "last_synced_at": "..."
  }
}
```

## 10.3 Profile primitives

The canonical profile must always support:

1. **Identity**
2. **Classification**
3. **Capabilities**
4. **Policies**
5. **Source/integration metadata**

### Do not embed the full product catalog directly into the profile.

The profile tells the agent:

> Who this merchant is, what they sell, what they can do, what rules apply, and where the data/capabilities can be queried.

Catalog and inventory remain separate resources.

---

# 11. Capability Contract

The profile declares what the merchant can do.

The capability contract defines exactly how each capability behaves.

## V0 capabilities

Only these six are required:

```text
1. discover
2. search_products
3. get_product
4. get_quote
5. create_cart
6. checkout
```

Future capabilities can include:

```text
track_order
cancel_order
refund
return
```

but they are not required for the V0 demo.

---

## 11.1 `discover`

Purpose:

Return machine-readable merchant metadata.

Example:

```json
{
  "merchant_id": "velocity-sports-001",
  "name": "Velocity Sports",
  "category": "sportswear",
  "capabilities": [
    "search_products",
    "get_product",
    "get_quote",
    "create_cart",
    "checkout"
  ],
  "policies": {
    "max_auto_purchase": 5000,
    "currency": "INR"
  }
}
```

---

## 11.2 `search_products`

Conceptual contract:

```text
search_products(
    merchant_id,
    query,
    filters,
    limit
)
```

Example result:

```json
{
  "products": [
    {
      "id": "prod-001",
      "title": "Nike Downshifter 14",
      "brand": "Nike",
      "category": "running_shoes",
      "variants": [
        {
          "id": "var-009",
          "size": "9",
          "color": "Black",
          "price": 4799,
          "currency": "INR",
          "available": true
        }
      ]
    }
  ]
}
```

---

## 11.3 `get_product`

Purpose:

Return authoritative/current product information needed for decision-making.

Must include:

- product ID;
- variant IDs;
- current price where available;
- currency;
- availability;
- relevant options;
- merchant URL.

---

## 11.4 `get_quote`

This is a critical boundary.

Purpose:

Generate a live transaction quote.

Conceptual input:

```text
product_id
variant_id
quantity
shipping_destination
```

Conceptual output:

```json
{
  "quote_id": "quote-123",
  "product_id": "prod-001",
  "variant_id": "var-009",
  "subtotal": 4799,
  "shipping": 0,
  "tax": 0,
  "total": 4799,
  "currency": "INR",
  "inventory_available": true,
  "expires_at": "...",
  "source": "shopify"
}
```

A quote is an explicit snapshot of merchant state at a point in time.

The policy engine must never authorize against stale natural-language model output.

---

## 11.5 `create_cart`

Purpose:

Create the transaction context needed for checkout.

Input:

```text
merchant_id
items
quote_id
```

Output:

```text
cart_id
cart_total
cart_state
```

---

## 11.6 `checkout`

This must be the most restricted capability.

It should not execute merely because the LLM called a function.

The backend must first verify:

```text
quote valid
AND
cart valid
AND
inventory valid
AND
policy allowed
AND
authorization valid
AND
amount unchanged
```

Only then may the payment provider be called.

---

# 12. Merchant Policies

Merchant policies are deterministic constraints.

## V0 minimum

```text
max_auto_purchase
requires_human_approval_above
allowed_categories
currency
return_window_days
allow_cancellation
```

Example:

```json
{
  "max_auto_purchase": 5000,
  "requires_human_approval_above": 5000,
  "allowed_categories": [
    "running_shoes",
    "sportswear"
  ],
  "currency": "INR",
  "return_window_days": 7,
  "allow_cancellation": true
}
```

The merchant can modify these in the dashboard.

---

# 13. Buyer Authorization

Merchant policy and buyer authorization are separate concepts.

Example:

```text
Merchant max auto-purchase = INR 10,000
Buyer max purchase          = INR 5,000
```

Effective limit:

```text
min(merchant_limit, buyer_limit)
= INR 5,000
```

If the merchant permits INR 10,000 but the user authorized only INR 5,000, the system must block any transaction above INR 5,000.

---

# 14. Policy Engine

The Policy Engine is deterministic Python code.

It must not use an LLM for final authorization.

## Inputs

```text
merchant policy
buyer authorization
merchant identity
product
variant
quote
transaction context
```

## V0 rules

```text
1. Merchant is active.
2. Product exists.
3. Variant exists.
4. Variant is currently available.
5. Quote is not expired.
6. Quote currency matches authorization currency.
7. Final amount <= buyer authorized amount.
8. Final amount <= merchant automatic-purchase limit.
9. Product category is allowed.
10. Merchant still satisfies the user constraint set.
11. Cart total matches the authorized/quoted total.
12. No material transaction field changed after authorization.
```

## Output

```json
{
  "allowed": false,
  "reason_codes": [
    "FINAL_AMOUNT_EXCEEDS_AUTHORIZATION"
  ],
  "explanation": "Final quote INR 5,799 exceeds the authorized INR 5,000 limit."
}
```

Every policy evaluation must become an audit event.

---

# 15. Transaction State Machine

The transaction is a strict state machine.

```text
DISCOVERED
    |
    v
PRODUCT_SELECTED
    |
    v
QUOTE_CREATED
    |
    v
POLICY_EVALUATED
    |
    +------------------------+
    |                        |
    v                        v
AUTHORIZED                 BLOCKED
    |
    v
CART_CREATED
    |
    v
PAYMENT_PENDING
    |
    +------------------------+
    |                        |
    v                        v
PAYMENT_SUCCESS          PAYMENT_FAILED
    |
    v
COMPLETED
```

## Important invariant

**No payment call is permitted unless the transaction is in an authorized state.**

This should be enforced at the service layer, not merely through UI logic.

---

# 16. Live Data vs Cached Data

Our PostgreSQL copy is a synchronized representation for discovery.

It is **not** the source of truth for final financial authorization.

## Discovery path

```text
Agent
  |
  v
Our DB
  |
  v
Search products
```

## Transaction path

```text
Agent
  |
  v
Our DB candidate
  |
  v
Shopify live validation
  |
  +--> current price
  +--> current inventory
  +--> variant validity
  |
  v
Create live quote
  |
  v
Policy Engine
```

This is mandatory for the demo because it gives us a clean way to demonstrate stale-data protection.

---

# 17. Shopify Integration

## 17.1 V0 scope

Only Shopify.

## 17.2 Merchant onboarding

Input:

```text
Shopify store URL
```

Flow:

```text
URL entered
   |
   v
Detect Shopify
   |
   v
Redirect to Shopify authorization
   |
   v
Merchant grants requested scopes
   |
   v
Receive authorization/session
   |
   v
Store encrypted credential server-side
   |
   v
Fetch merchant metadata
   |
   v
Sync product/catalog data
```

Shopify app installations expose the permissions/scopes granted to the app, and access tokens are scoped credentials enforced by Shopify.

## 17.3 Sync

V0 sync should capture only what is necessary:

- merchant name;
- domain;
- product title;
- description;
- product category/type;
- brand/vendor if available;
- product ID;
- variant ID;
- SKU if available;
- price;
- currency;
- availability/inventory;
- size/color/options;
- image URL;
- product URL.

## 17.4 Source-of-truth requirement

Use our database for search/discovery.

Use Shopify for transaction-time validation.

## 17.5 Future adapters

The adapter interface should conceptually be:

```python
class CommerceAdapter:
    def get_merchant_profile(...): ...
    def sync_catalog(...): ...
    def search_products(...): ...
    def get_product(...): ...
    def get_live_quote(...): ...
    def create_cart(...): ...
    def create_or_finalize_order(...): ...
```

V0 implements only:

```text
ShopifyAdapter
```

Future implementations could be:

```text
WooCommerceAdapter
CustomApiAdapter
MagentoAdapter
```

No future adapter code is required for V0.

---

# 18. Razorpay Integration

Razorpay is a payment rail inside the system, not the product's center of gravity.

## 18.1 Provider interface

```python
class PaymentProvider:
    def create_order(...): ...
    def get_payment(...): ...
    def capture_payment(...): ...
```

## 18.2 Razorpay implementation

```text
RazorpayProvider
```

The implementation should use Test Mode/Test Keys during the buildathon. Razorpay's current docs expose test keys, Orders APIs, Payments APIs, and capture for authorized payments.

## 18.3 Payment invariant

The amount sent to Razorpay must come from the **validated quote/authorized transaction state**, never directly from LLM-generated text.

---

# 19. Database Schema

## `merchants`

```text
id
name
slug
description
category
subcategory
website_url
logo_url
status
agent_endpoint
created_at
updated_at
```

## `merchant_integrations`

```text
id
merchant_id
provider
store_url
auth_reference_encrypted
scopes_json
status
last_synced_at
created_at
updated_at
```

Never expose encrypted credentials to the agent runtime.

## `merchant_capabilities`

```text
id
merchant_id
capability_name
enabled
version
config_json
created_at
updated_at
```

## `merchant_policies`

```text
id
merchant_id
max_auto_purchase
approval_threshold
allowed_categories_json
allowed_regions_json
currency
return_window_days
allow_cancellation
version
created_at
updated_at
```

## `products`

```text
id
merchant_id
external_id
title
description
category
brand
product_url
image_url
status
source
source_updated_at
created_at
updated_at
```

## `product_variants`

```text
id
product_id
external_id
sku
title
price
currency
available_quantity
available_for_sale
options_json
source_updated_at
created_at
updated_at
```

## `agent_sessions`

```text
id
session_id
buyer_id
merchant_id
user_intent
constraints_json
status
started_at
ended_at
```

## `quotes`

```text
id
merchant_id
product_id
variant_id
subtotal
shipping_amount
tax_amount
total_amount
currency
inventory_snapshot
source
expires_at
created_at
```

## `carts`

```text
id
merchant_id
session_id
external_cart_id
items_json
total_amount
currency
status
created_at
updated_at
```

## `transactions`

```text
id
session_id
merchant_id
product_id
variant_id
quote_id
cart_id
requested_amount
quoted_amount
authorized_amount
final_amount
currency
status
shopify_reference
razorpay_order_id
razorpay_payment_id
created_at
updated_at
```

## `transaction_events`

```text
id
transaction_id
event_type
actor
timestamp
payload_json
```

---

# 20. Transaction Event Model

Minimum event types:

```text
USER_INTENT
MERCHANT_DISCOVERED
PRODUCT_SEARCHED
PRODUCT_SELECTED
QUOTE_CREATED
POLICY_EVALUATED
AUTHORIZATION_GRANTED
AUTHORIZATION_DENIED
CART_CREATED
PAYMENT_ORDER_CREATED
PAYMENT_AUTHORIZED
PAYMENT_CAPTURED
PAYMENT_FAILED
TRANSACTION_BLOCKED
TRANSACTION_COMPLETED
```

Each event should include:

```json
{
  "actor": "buyer_agent",
  "timestamp": "...",
  "reason": "...",
  "payload": {}
}
```

The audit trail must answer:

- what did the user ask for?
- what did the agent discover?
- what did the agent select?
- what did the merchant say the price was?
- what policy was evaluated?
- what was approved/denied?
- what payment action happened?
- why did the transaction stop, if it stopped?

---

# 21. Buyer Agent Tools

The agent gets only controlled tools.

## Tool: `discover_merchant`

Returns Merchant Agent Profile.

## Tool: `search_products`

Searches the canonical catalog.

## Tool: `get_product`

Retrieves normalized product/variant data.

## Tool: `get_quote`

Calls the adapter for a live quote.

## Tool: `create_cart`

Creates a cart/transaction context.

## Tool: `checkout`

Routes through policy validation and payment orchestration.

### Security rule

The agent cannot:

- call Shopify directly;
- call Razorpay directly;
- modify merchant policies;
- modify transaction amounts;
- bypass policy checks;
- write to the database.

---

# 22. Agent Interaction Contract

The interaction should look conceptually like:

```text
BUYER AGENT
    |
    | discover merchant
    v
GATEWAY
    |
    | Merchant Agent Profile
    v
BUYER AGENT
    |
    | search_products("Nike Downshifter 14")
    v
GATEWAY
    |
    v
SHOPIFY ADAPTER
    |
    v
PRODUCT CANDIDATES
    |
    v
BUYER AGENT
    |
    | select size 9
    v
GATEWAY
    |
    | get_quote
    v
SHOPIFY LIVE STATE
    |
    v
QUOTE
    |
    v
POLICY ENGINE
```

The buyer agent is intelligent; the gateway is authoritative.

---

# 23. Merchant Dashboard Requirements

## Page 1 - Connect Store

Fields:

- Shopify store URL
- connect button

States:

- detecting;
- Shopify detected;
- authorization pending;
- connected;
- failed.

## Page 2 - AI-Native Profile

Display:

- merchant identity;
- category;
- description;
- website;
- capabilities;
- source;
- sync status;
- last sync time.

## Page 3 - Policies

Merchant can configure:

- maximum automatic purchase amount;
- approval threshold;
- allowed categories;
- currency;
- return window;
- cancellation policy.

## Page 4 - Agent Test Console

Show a simple prompt box:

> Find me Nike Downshifter 14, size 9, under INR 5,000, with a good return policy.

Button:

```text
Run Buyer Agent
```

## Page 5 - Transaction Trace

Render the transaction timeline from `transaction_events`.

Example:

```text
16:31:02 USER_INTENT
"Buy Nike Downshifter 14..."

16:31:03 MERCHANT_DISCOVERED
Velocity Sports

16:31:04 PRODUCT_SELECTED
Nike Downshifter 14 / Size 9

16:31:05 QUOTE_CREATED
INR 4,799

16:31:05 POLICY_EVALUATED
PASS

16:31:06 AUTHORIZATION_GRANTED
INR 4,799

16:31:07 PAYMENT_CREATED
Razorpay Test Order

16:31:12 PAYMENT_SUCCESS

16:31:12 TRANSACTION_COMPLETED
```

---

# 24. Primary Demo Scenario

The entire demo should revolve around one recognizable purchase.

## User request

> **"Find me Nike Downshifter 14, size 9, under INR 5,000. I want the best deal and a reliable return policy. Buy it for me."**

## Expected behavior

### Step 1 - Discovery

Buyer agent identifies the connected merchant.

### Step 2 - Search

Agent searches product catalog.

### Step 3 - Selection

Agent selects an eligible size/variant.

### Step 4 - Live quote

System obtains authoritative current quote.

### Step 5 - Policy evaluation

Policy engine checks all constraints.

### Step 6 - Authorization

Transaction is authorized because:

```text
price <= user limit
price <= merchant limit
inventory = available
category = allowed
quote = valid
```

### Step 7 - Payment

Razorpay Test Mode order/payment flow is invoked.

### Step 8 - Audit

All events are rendered in a readable timeline.

---

# 25. Mandatory Failure Demo

The project must intentionally demonstrate that the LLM cannot override deterministic financial controls.

## Scenario

Agent initially receives:

```text
Nike Downshifter 14
Price = INR 4,799
```

User authorization:

```text
Maximum spend = INR 5,000
```

Before checkout, the live merchant price is changed/mocked to:

```text
INR 5,799
```

## Expected behavior

The system detects:

```text
quoted_amount = 4,799
current_amount = 5,799
```

Policy result:

```text
BLOCKED
```

The Razorpay payment call must **never happen**.

Agent-facing explanation:

> The price changed from INR 4,799 to INR 5,799 after authorization, so I did not complete the payment.

Audit:

```text
QUOTE_CREATED
POLICY_EVALUATED
AUTHORIZATION_DENIED
TRANSACTION_BLOCKED
```

This is one of the most important moments of the demo.

---

# 26. Optional Second Failure

If time permits:

### Inventory race

Initial state:

```text
size 9 available = true
```

Before checkout:

```text
size 9 available = false
```

Expected:

```text
BLOCKED
reason = INVENTORY_UNAVAILABLE
```

Do not implement more failure types unless they improve the demo.

---

# 27. API Surface

## Merchant onboarding

```http
POST /api/v1/onboarding/shopify/connect
POST /api/v1/onboarding/shopify/callback
GET  /api/v1/merchants/{merchant_id}/profile
POST /api/v1/merchants/{merchant_id}/sync
```

## Merchant configuration

```http
GET  /api/v1/merchants/{merchant_id}/capabilities
GET  /api/v1/merchants/{merchant_id}/policies
PUT  /api/v1/merchants/{merchant_id}/policies
```

## Agent gateway

```http
GET  /api/v1/merchants/{merchant_id}/discover
POST /api/v1/merchants/{merchant_id}/search
GET  /api/v1/merchants/{merchant_id}/products/{product_id}
POST /api/v1/merchants/{merchant_id}/quote
POST /api/v1/merchants/{merchant_id}/cart
POST /api/v1/merchants/{merchant_id}/checkout
```

## Transactions

```http
GET /api/v1/transactions/{transaction_id}
GET /api/v1/transactions/{transaction_id}/events
```

---

# 28. Idempotency Requirements

Payment and transaction APIs must be idempotent.

For example:

```text
POST /checkout
Idempotency-Key: tx-92831
```

A retry must not create multiple payments.

At minimum:

- checkout requests;
- Razorpay order creation;
- transaction finalization.

must be protected against duplicate execution.

---

# 29. Security Requirements

## Critical

1. Never expose Shopify access tokens to the buyer agent.
2. Never expose Razorpay secrets to the buyer agent.
3. Never store raw customer payment credentials.
4. Never let the LLM directly choose an arbitrary payment amount.
5. Never allow the LLM to bypass policy checks.
6. Revalidate live merchant state before payment.
7. Record every authorization decision.
8. Validate every external input with typed schemas.
9. Use encrypted storage for merchant credentials/secrets.
10. Use server-side secret management for all API keys.

## Principle

```text
LLM decides intent/action proposal
        |
        v
Gateway validates request
        |
        v
Policy Engine authorizes/denies
        |
        v
Payment Provider executes
```

---

# 30. Observability

For the demo, do not build an enterprise observability platform.

Implement:

- structured application logs;
- transaction IDs;
- session IDs;
- event IDs;
- API request IDs;
- clear policy decision logs.

Every user-visible transaction should be traceable using one `transaction_id`.

---

# 31. Testing Strategy

## 31.1 Unit tests

Must cover:

- MerchantAgentProfile validation;
- capability validation;
- quote validation;
- policy engine;
- state machine transitions;
- amount mismatch;
- inventory mismatch;
- idempotency.

## 31.2 Integration tests

Must cover:

- Shopify adapter against a test/dev store or controlled fixture;
- Razorpay Test Mode order creation;
- payment status handling;
- event creation.

## 31.3 End-to-end test set

Create at least 20 scenarios.

Example:

```text
10 valid purchases
5 price mismatch cases
3 inventory failures
2 policy violations
```

Expected property:

```text
0 unauthorized payments
```

The exact counts are less important than demonstrating that the system reliably blocks prohibited financial actions.

---

# 32. Evaluation Metrics

Track at least:

```text
transaction_attempts
successful_transactions
blocked_transactions
policy_violations_detected
unauthorized_payment_attempts
false_blocks
quote_mismatches
average_policy_evaluation_latency
```

For the buildathon demo, emphasize:

### Safety

```text
Unauthorized payments attempted = 0
```

### Reliability

```text
Live quote validated before payment = 100%
```

### Auditability

```text
Transactions with complete audit trail = 100%
```

These numbers should come from actual test runs, not fabricated claims.

---

# 33. Project Structure

Recommended monorepo:

```text
agent-commerce/
|
+-- apps/
|   +-- web/                         # React + Vite
|   +-- api/                         # FastAPI
|
+-- packages/
|   +-- contracts/                   # Pydantic/shared schemas
|   +-- policy-engine/               # deterministic rules
|   +-- agent/                       # buyer agent + tool definitions
|   +-- adapters/
|       +-- shopify/
|       +-- razorpay/
|
+-- database/
|   +-- migrations/
|   +-- seeds/
|
+-- docs/
|   +-- architecture.md
|   +-- merchant-profile.md
|   +-- gateway-contract.md
|
+-- docker-compose.yml
+-- .env.example
+-- README.md
```

If a simpler structure improves velocity, a single FastAPI application with module boundaries is acceptable. Do not create artificial package complexity.

---

# 34. Implementation Order

## Phase 1 - Domain contracts

Build first:

- MerchantAgentProfile
- Capability
- MerchantPolicy
- BuyerAuthorization
- Product
- ProductVariant
- Quote
- Cart
- Transaction
- TransactionEvent

Then define the transaction state machine.

Do not start with UI.

## Phase 2 - Mock merchant adapter

Create a fake Shopify-like adapter using static fixture data.

Goal:

```text
agent
 -> search
 -> product
 -> quote
 -> policy
 -> checkout
 -> payment mock
```

The purpose is to validate our abstraction before integrating Shopify.

## Phase 3 - Shopify adapter

Implement:

- authorization;
- merchant metadata;
- product sync;
- variant sync;
- inventory sync;
- live product validation;
- live quote.

## Phase 4 - Real Razorpay Test Mode

Implement the payment provider adapter.

## Phase 5 - Buyer agent

Implement controlled tool-calling agent.

## Phase 6 - Merchant dashboard

Implement the demo-friendly UI.

## Phase 7 - Failure simulation

Add deterministic price mutation and/or inventory mutation for demo purposes.

## Phase 8 - Hardening

Add:

- idempotency;
- better error handling;
- event consistency;
- logging;
- test coverage.

## Phase 9 - Demo polish

No major new features.

Focus on:

- speed;
- clarity;
- trace visibility;
- UX;
- pitchability.

---

# 35. Definition of Done

The project is demo-ready when all of the following are true:

### Merchant onboarding

- [ ] Shopify store URL can be submitted.
- [ ] Shopify authorization works.
- [ ] Merchant metadata is loaded.
- [ ] Product catalog is synchronized.
- [ ] Merchant Agent Profile is generated.
- [ ] Merchant can edit V0 policies.

### Agent gateway

- [ ] Buyer agent can discover merchant.
- [ ] Buyer agent can search products.
- [ ] Buyer agent can retrieve a product.
- [ ] Buyer agent can retrieve a live quote.
- [ ] Buyer agent can create transaction/cart context.
- [ ] Buyer agent can request checkout.

### Safety

- [ ] All checkout attempts pass through Policy Engine.
- [ ] LLM cannot directly call payment provider.
- [ ] Final amount is validated against authorization.
- [ ] Live quote is revalidated before payment.
- [ ] Price mismatch is blocked.

### Payment

- [ ] Razorpay Test Mode order can be created.
- [ ] Payment result can be recorded.
- [ ] Payment success/failure is reflected in transaction state.

### Auditability

- [ ] Every transaction creates events.
- [ ] Policy evaluation is visible.
- [ ] Payment action is visible.
- [ ] Block reason is visible.

### Demo

- [ ] Successful purchase flow works end-to-end.
- [ ] Failure flow works end-to-end.
- [ ] Demo can be completed without terminal commands.
- [ ] Architecture can be explained in under 2 minutes.

---

# 36. Demo Story

The demo should tell a simple story instead of explaining every implementation detail.

## Scene 1 - The old problem

Show a normal Shopify merchant.

Explain:

> The store works perfectly for humans, but an AI buyer has no native commerce interface.

## Scene 2 - Make it AI-native

Merchant enters store URL.

Click:

```text
Make AI-Native
```

Profile appears.

## Scene 3 - Configure the guardrails

Merchant chooses:

```text
Maximum automatic purchase: INR 5,000
Approval above: INR 5,000
Return window: 7 days
```

## Scene 4 - AI buyer

Enter:

> Find Nike Downshifter 14, size 9, under INR 5,000, with a reliable return policy.

Let the agent discover/search/quote/select.

## Scene 5 - Payment

Policy passes.

Razorpay test payment happens.

## Scene 6 - Failure

Change price to INR 5,799.

Run again.

System blocks payment.

Show:

```text
AUTHORIZED: INR 5,000
CURRENT QUOTE: INR 5,799
RESULT: BLOCKED
```

## Scene 7 - Audit trail

Show all events in sequence.

Closing line:

> **We didn't build another shopping chatbot. We built an agent layer that makes an ordinary merchant AI-native without rebuilding the merchant's human-facing store.**

---

# 37. What Makes This Technically Interesting

The differentiating ideas are not:

- calling an LLM;
- using Shopify APIs;
- calling Razorpay APIs;
- making a chatbot.

The interesting engineering is the boundary between:

```text
Probabilistic Agent Reasoning
                |
                v
Canonical Agent Commerce Interface
                |
                v
Deterministic Policy Enforcement
                |
                v
Financial Execution
                |
                v
Auditable State
```

The merchant can keep its existing human storefront while exposing a completely different machine-facing interface.

---

# 38. Future Architecture Beyond V0

V0 only supports Shopify, but the canonical architecture should make future adapters possible:

```text
                    Agent Commerce Gateway
                              |
          +-------------------+-------------------+
          |                   |                   |
          v                   v                   v
     ShopifyAdapter     WooCommerceAdapter    CustomAdapter
          |                   |                   |
          v                   v                   v
       Merchant             Merchant             Merchant
```

Potential future capabilities:

- merchant self-service onboarding;
- automatic website ingestion;
- custom API adapters;
- merchant agent manifests;
- agent identity/trust;
- richer A2A interoperability;
- order tracking;
- refunds/returns;
- settlement integrations;
- multiple payment providers;
- agent discovery/merchant ranking;
- merchant AI-readiness score;
- merchant analytics on agent-driven conversion.

None of these are required for the buildathon V0.

---

# 39. Architectural Decisions to Preserve

## ADR-01: Canonical commerce model

**Decision:** Buyer agents interact with a platform-neutral canonical model.

**Reason:** Prevent Shopify-specific logic from leaking into the agent layer.

## ADR-02: Modular monolith

**Decision:** Single FastAPI application with module boundaries.

**Reason:** One-week build speed and reduced operational complexity.

## ADR-03: LLM cannot authorize payment

**Decision:** Deterministic Policy Engine controls financial execution.

**Reason:** LLM output is probabilistic and cannot be the final source of financial authorization.

## ADR-04: Live validation before payment

**Decision:** Cached product data may drive discovery, but Shopify state is revalidated before checkout.

**Reason:** Prevent stale price/inventory from causing incorrect payment.

## ADR-05: Razorpay as provider adapter

**Decision:** Razorpay sits behind `PaymentProvider`.

**Reason:** The product is merchant agent infrastructure, not a payment API wrapper.

## ADR-06: Shopify only in V0

**Decision:** Only one real integration.

**Reason:** Demonstrate depth rather than breadth.

## ADR-07: Audit as a first-class domain object

**Decision:** Store semantic transaction events.

**Reason:** The track explicitly values explainability and graceful failure handling.

---

# 40. Important Constraints for the Coding Agent

The coding agent should follow these rules:

1. **Do not expand scope without a clear reason.**
2. **Do not add a second commerce platform during V0.**
3. **Do not allow the LLM to call external APIs directly.**
4. **Do not make payment amount a free-form LLM parameter.**
5. **Do not treat generated/natural-language product information as authoritative at payment time.**
6. **Do not duplicate Shopify-specific logic throughout the codebase.**
7. **Do not add microservices unless an actual implementation blocker requires them.**
8. **Do not add Kafka/Kubernetes/vector databases for the demo.**
9. **Use typed contracts everywhere.**
10. **Every financial state transition must be auditable.**
11. **Every checkout request must be idempotent.**
12. **Prefer a working narrow path over broad incomplete features.**
13. **The demo flow must work end-to-end from the UI.**
14. **Keep external secrets server-side.**
15. **Make failure handling as visible as the happy path.**

---

# 41. Final Product Definition

## What are we building?

A merchant AI layer that transforms an ordinary Shopify merchant into an **AI-discoverable, AI-interactable, and AI-transactable merchant**.

## How?

By:

1. connecting to the merchant's existing Shopify infrastructure;
2. normalizing commerce data into a canonical merchant model;
3. generating a Merchant Agent Profile;
4. exposing standardized agent capabilities through an Agent Commerce Gateway;
5. allowing buyer agents to discover/search/interact with the merchant;
6. revalidating live merchant state before purchase;
7. enforcing deterministic merchant and buyer policies;
8. routing authorized payment to Razorpay Test Mode;
9. and recording a complete transaction audit trail.

## What is the V0?

```text
ONE MERCHANT
    = Shopify

ONE DOMAIN
    = E-commerce / footwear

ONE BUYER FLOW
    = product discovery -> purchase

ONE PAYMENT PROVIDER
    = Razorpay Test Mode

ONE POLICY LAYER
    = deterministic authorization

ONE AUDIT MODEL
    = transaction events

ONE FAILURE DEMO
    = price mismatch / policy block
```

## Final thesis

> **A merchant should not need to rebuild its website for the agentic era. Connect the store once, generate an agent-native interface, configure the rules, and let autonomous buyers interact and transact through a safe, deterministic gateway.**

---

# 42. Official Reference Sources

Use current official documentation during implementation because API versions and platform behavior can change.

### Shopify

- Shopify Admin GraphQL / AppInstallation: https://shopify.dev/docs/api/admin-graphql/latest/objects/AppInstallation
- Shopify AppInstallation query: https://shopify.dev/docs/api/admin-graphql/latest/queries/appInstallation
- Shopify access tokens: https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens

### Razorpay

- Razorpay API reference: https://razorpay.com/docs/api/
- Razorpay Orders API: https://razorpay.com/docs/api/orders/
- Razorpay Payments API: https://razorpay.com/docs/api/payments/
- Razorpay Capture API: https://razorpay.com/docs/api/payments/capture/

### Implementation note

The coding agent must verify exact API version, scopes, mutations, checkout/order capabilities, and current authentication requirements immediately before implementation rather than relying on this PRD for undocumented request shapes.

---

# 43. Handoff Instruction to Coding Agent

**Build the system described above as a working Shopify-only V0.**

Start by implementing the domain contracts and transaction state machine, then build a mock adapter to validate the architecture, then replace it with the Shopify adapter, then integrate Razorpay Test Mode, then build the buyer-agent tools, then finish the merchant dashboard and demo flows.

At every step:

```text
Prefer correctness > feature breadth
Prefer deterministic contracts > implicit behavior
Prefer one complete vertical slice > multiple partial integrations
Prefer explainable failures > hidden recovery
```

The final result must be demonstrable entirely from the UI and must visibly prove the central thesis:

```text
NORMAL SHOPIFY MERCHANT
          |
          v
      CONNECT STORE
          |
          v
   AI-NATIVE PROFILE
          |
          v
   AGENT COMMERCE GATEWAY
          |
          v
      BUYER AGENT
          |
          v
     POLICY ENGINE
          |
          v
      RAZORPAY
          |
          v
     AUDIT TRAIL
```

**End of PRD.**
