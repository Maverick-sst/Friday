# PRD AMENDMENT — UCP/ACP ALIGNMENT & MERCHANT ENABLEMENT LAYER

**Status:** Critical architecture update  
**Applies to:** Existing Agent Commerce Gateway / Shopify V0 PRD  
**Instruction to coding agent:** Read this amendment before making further architectural decisions. Do not discard the existing implementation unnecessarily. Preserve working components where possible, but realign the protocol layer with existing industry standards.

---

# 1. Why This Amendment Exists

The original PRD was written around the idea of creating a canonical merchant-agent interface and gateway for agentic commerce.

Since then, we identified that the industry has already moved significantly in this direction through emerging open standards, particularly:

- **Universal Commerce Protocol (UCP)** from Google and ecosystem partners including Shopify.
- **Agentic Commerce Protocol (ACP)** from OpenAI and Stripe.

UCP was introduced as an open standard for agentic commerce connecting consumer/agent surfaces, businesses, and payment providers. It supports integration through APIs, A2A, and MCP and is designed to work with existing retail infrastructure.

ACP was introduced by OpenAI and Stripe as an open standard allowing AI agents, people, and businesses to work together to complete purchases, with the merchant retaining control of its backend, payments, fulfillment, and customer relationship.

Shopify has subsequently implemented UCP-compatible MCP infrastructure and provides tooling for agents to discover products, build carts, create checkouts, and track orders.

**Therefore, this project must NOT attempt to reinvent a new agent-commerce protocol.**

---

# 2. Updated Product Thesis

## Original thesis

> Turn a normal merchant into an AI-native merchant.

## Refined thesis

> **Turn a normal merchant into an AI-ready merchant by automatically adapting their existing commerce infrastructure to emerging agentic-commerce standards such as UCP/ACP.**

The fundamental problem remains unchanged:

```text
Traditional merchant
        ↓
Human-first website / backend
        ↓
AI agents cannot reliably transact
```

Our solution becomes:

```text
Traditional merchant
        ↓
Our Merchant AI Enablement Layer
        ↓
UCP / ACP-compatible commerce surface
        ↓
AI agents
        ↓
Discovery → Interaction → Transaction
```

We are therefore building the **merchant enablement / adaptation layer**, not another competing commerce protocol.

---

# 3. What We Are NOT Building

Do NOT spend implementation time creating:

- a new proprietary agent-commerce standard
- a new universal agent-to-merchant protocol
- another replacement for UCP
- another replacement for ACP
- a new generic MCP-like protocol
- a generalized A2A protocol

Our internal schemas and canonical models may still exist because they are useful engineering abstractions.

However:

> **Internal canonical models are implementation details, not a new public protocol.**

The externally exposed interface should align with relevant existing standards wherever practical.

---

# 4. Updated Product Positioning

The product should be understood as:

> **"Make your store AI-native in minutes."**

A merchant provides their existing commerce infrastructure.

For V0, this means a **Shopify merchant**.

Our platform:

```text
Detect Shopify
      ↓
Authorize access
      ↓
Understand merchant
      ↓
Normalize commerce data
      ↓
Map capabilities
      ↓
Configure policies
      ↓
Expose / adapt to agentic commerce standards
      ↓
Test with an AI buyer
```

The merchant should not need to rebuild their website.

---

# 5. The New Architectural Principle

Use this hierarchy:

```text
                    AI BUYER
                       │
                       ▼
              UCP / ACP INTERFACE
                       │
                       ▼
        ┌──────────────────────────────┐
        │ OUR PLATFORM                 │
        │                              │
        │ Merchant AI Enablement Layer │
        └──────────────┬───────────────┘
                       │
              Merchant Adapter
                       │
                       ▼
                    SHOPIFY
```

Our platform's job is to make the merchant compatible with the agentic-commerce ecosystem.

UCP/ACP provide interoperability.

Shopify remains the commerce backend.

Razorpay remains a payment rail / provider in the demonstration.

---

# 6. UCP Is Especially Relevant to the Shopify V0

UCP currently provides a business profile and capability-negotiation model. Shopify's current documentation describes a merchant/business profile available at:

`/.well-known/ucp`

and explains that UCP negotiation involves the agent/platform profile and the business profile.

Shopify's current UCP tooling already supports:

- agent authentication
- catalog discovery
- cart creation
- checkout
- order tracking

and Shopify explicitly provides UCP-compliant MCP servers for these flows.

Therefore, the implementation should investigate and reuse these existing Shopify/UCP mechanisms instead of building an equivalent interface from scratch.

---

# 7. Important Implication: The Merchant Profile Is No Longer Our Proprietary Protocol

The original architecture envisioned:

```text
Merchant
   ↓
Our Merchant Agent Profile
   ↓
Our Gateway
```

The updated architecture is:

```text
Merchant
   ↓
Our internal merchant representation
   ↓
UCP / ACP-compatible surface
   ↓
Agent ecosystem
```

Our internal `Merchant` model should still contain:

```text
identity
category
description
website
capabilities
policies
integration source
```

But we should map those fields into standards-compliant representations where possible.

Do not create unnecessary proprietary fields or protocol semantics when UCP/ACP already provide the corresponding concept.

---

# 8. Updated Capability Strategy

The original V0 capabilities remain conceptually correct:

```text
discover
search_products
get_product
get_quote
create_cart
checkout
```

However, these should be implemented with awareness of the corresponding UCP / Shopify MCP capabilities.

For example:

```text
Our internal abstraction
        ↓
UCP capability
        ↓
Shopify UCP/MCP implementation
```

Rather than:

```text
Our proprietary capability protocol
        ↓
Shopify
```

Shopify's current Cart MCP implements the UCP cart capability, while Checkout MCP implements the UCP checkout capability.

---

# 9. Updated Gateway Responsibility

Our gateway should focus on what UCP/ACP do NOT eliminate:

### Merchant onboarding

```text
Shop URL
   ↓
Platform detection
   ↓
Authorization
   ↓
Merchant setup
```

### Merchant normalization

```text
Shopify data
   ↓
Our internal representation
```

### Merchant AI-readiness

```text
Catalog
Inventory
Capabilities
Policies
Checkout
```

### Configuration

Merchant decides:

```text
maximum autonomous spend
approval threshold
allowed transaction categories
regions
business policies
```

### Testing / simulation

Merchant can test:

> "What would an AI buyer experience when interacting with my store?"

### Observability

Show:

```text
agent request
capability negotiation
product discovery
quote
policy decision
checkout
payment
result
```

This is the layer where our product differentiation should live.

---

# 10. The New Core Product Question

Do not ask:

> "How can we create our own agent-commerce gateway?"

Ask:

> **"How can we make an ordinary Shopify merchant interoperable with agentic commerce in minutes?"**

This is now the central engineering question.

---

# 11. Updated Shopify V0 Flow

The V0 demo should become:

```text
Merchant
   │
   │ Shopify URL
   ▼
Our Platform
   │
   │ Detect Shopify
   ▼
Merchant Authorization
   │
   ▼
Import / Map:
   ├── merchant identity
   ├── products
   ├── variants
   ├── inventory
   ├── policies
   └── commerce capabilities
   │
   ▼
Generate AI-readiness representation
   │
   ▼
Map to UCP-compatible capabilities
   │
   ▼
Publish / expose agent-facing surface
   │
   ▼
AI Buyer
   │
   ▼
Discovery
   │
   ▼
Product selection
   │
   ▼
Cart
   │
   ▼
Checkout
   │
   ▼
Policy validation
   │
   ▼
Payment
   │
   ▼
Audit trail
```

---

# 12. Important Shopify Reality

Shopify now provides native UCP tooling.

Its current documentation describes a UCP CLI and Shopify AI Toolkit capable of walking an agent through discovery → cart → checkout → order tracking.

Shopify also documents that carts and checkout for agents can hand the buyer back to the merchant's checkout, while eligible flows may complete checkout directly.

Therefore:

> **Do not rebuild Shopify's UCP implementation just to demonstrate that UCP works.**

Instead, determine what merchant-side value our platform adds on top of / around it.

---

# 13. Revised Differentiation Hypothesis

Our strongest possible differentiation is:

## "Merchant AI Enablement"

A merchant shouldn't need to understand:

- UCP
- ACP
- MCP
- A2A
- agent profiles
- capability negotiation
- commerce schemas
- agent authentication

They should simply see:

```text
Connect your Shopify store
        ↓
We make it AI-ready
        ↓
Configure your rules
        ↓
Test your store with an AI buyer
```

The complexity lives underneath our platform.

---

# 14. Updated Policy Engine Role

The policy engine remains a core part of the project.

This does NOT become obsolete just because UCP/ACP exist.

The LLM/agent can still be probabilistic:

```text
discover
reason
compare
recommend
select
```

But transaction authorization must remain deterministic:

```text
price
budget
merchant
category
authorization
quote validity
inventory
```

Example:

```text
Authorized maximum: ₹5,000
Live merchant quote: ₹5,799

→ BLOCK
```

The important architectural rule remains:

> **The agent may request a financial action. It cannot unilaterally authorize that financial action.**

---

# 15. Updated Role of Razorpay

Razorpay is not the product.

Razorpay is the financial execution layer in the demonstration.

```text
AI Agent
   ↓
UCP / ACP commerce interaction
   ↓
Our merchant enablement layer
   ↓
Deterministic policy
   ↓
Razorpay
   ↓
Payment
```

The product value remains merchant AI-readiness.

This also aligns better with Razorpay's broader direction: Razorpay is already actively building agentic payment infrastructure and AI-native payment experiences. Therefore, the project should demonstrate complementary merchant-side infrastructure rather than simply recreating Razorpay's agentic-payment work.

---

# 16. Critical New Research / Implementation Task

Before making further architectural changes, investigate exactly what the current standards and Shopify implementation already provide.

The coding agent MUST review:

1. UCP merchant/business profile
2. UCP capability negotiation
3. UCP catalog/discovery capability
4. UCP Cart capability
5. UCP Checkout capability
6. Shopify UCP MCP servers
7. Shopify `.well-known/ucp`
8. ACP merchant integration model
9. Where ACP and UCP overlap
10. What merchant-side functionality is still missing / painful

Do not duplicate functionality simply because it was present in the original PRD.

---

# 17. What We Should Try to Demonstrate

The strongest demo is no longer:

> "Look, we invented a protocol for agents to buy things."

It should be:

> **"Here is an ordinary Shopify merchant. Give us its store. We make it agent-ready in minutes, configure its autonomous-commerce policies, and demonstrate an AI buyer discovering and transacting with it through the emerging agentic-commerce standards."**

Potential demo:

```text
SHOPIFY MERCHANT

Connect Store
      ↓
AI Readiness Scan
      ↓
Merchant Profile
      ↓
Capabilities
      ↓
Policies
      ↓
Publish / Enable
      ↓
TEST WITH AI BUYER

"Find Nike Downshifter 14,
size 9, under ₹5,000."

      ↓

Agent discovers merchant
      ↓
Agent discovers product
      ↓
Agent evaluates options
      ↓
Quote
      ↓
Policy Engine
      ↓
Checkout
      ↓
Razorpay Test Payment
      ↓
Audit Trail
```

---

# 18. Failure Demo Remains Mandatory

Keep the existing failure demonstration.

Example:

```text
Agent authorization:
₹4,799

Merchant live quote:
₹5,799

Policy:
FAIL

Payment:
NOT EXECUTED
```

Explain:

> "The agent was allowed to reason about the purchase, but the financial action crossed the deterministic authorization boundary."

This remains one of the strongest parts of the project.

---

# 19. Architectural Direction After This Amendment

The system should now conceptually become:

```text
                      BUYER AGENT
                           │
                           ▼
                    UCP / ACP
                           │
                           ▼
            ┌─────────────────────────┐
            │ MERCHANT AI ENABLEMENT  │
            │                         │
            │ • onboarding            │
            │ • normalization         │
            │ • policy configuration   │
            │ • capability mapping    │
            │ • testing               │
            │ • observability         │
            └────────────┬────────────┘
                         │
                         ▼
                  SHOPIFY ADAPTER
                         │
                         ▼
                      SHOPIFY
                         │
                         ▼
                 Payment / Checkout
                         │
                         ▼
                    RAZORPAY
```

The internal architecture can still contain:

```text
Merchant
Product
Variant
Capability
Policy
Quote
Transaction
TransactionEvent
```

Those remain valid domain models.

The change is that they should now be **mapped to external standards rather than presented as our own competing protocol.**

---

# 20. Final Strategic Direction

### We are NOT building:

**A new agent-commerce protocol.**

### We ARE building:

**A merchant AI-readiness and adaptation layer.**

### V0 integration:

**Shopify only.**

### External standards:

**UCP-first; understand ACP interoperability and differences.**

### Core merchant value:

**Become AI-discoverable, AI-interactable and AI-transactable without rebuilding the existing commerce stack.**

### Core differentiator:

**Make this easy enough that a merchant can become AI-ready in minutes.**

### Core safety mechanism:

**Deterministic transaction policy engine.**

### Payment demonstration:

**Razorpay Test Mode.**

### Core demo:

**Connect Shopify → become agent-ready → AI buyer discovers/shops → policy validation → checkout/payment → auditable trace.**

---

# 21. Instruction to the Coding Agent

**Do not stop or discard the current build blindly.**

Instead:

1. Read this amendment.
2. Map the existing implementation against UCP/ACP/Shopify's current capabilities.
3. Identify anything we have built that directly duplicates existing UCP/ACP functionality.
4. Preserve useful internal abstractions.
5. Refactor only where necessary.
6. Prioritize demonstrating the merchant enablement/adaptation layer.
7. Do not implement unnecessary protocol functionality ourselves.
8. Before adding any new agent-commerce interface, verify whether UCP already defines the corresponding capability.

The objective is not maximum standards compliance for its own sake.

The objective is:

> **Build the smallest compelling product that proves we can take an ordinary Shopify merchant and make it genuinely ready for the emerging agentic-commerce ecosystem.**

---

## Important ecosystem context

UCP is now a significant industry initiative: Google describes it as an open standard for agentic commerce, designed to connect consumer/agent surfaces, businesses, and payment providers, with Shopify among the collaborators.

ACP is the complementary OpenAI/Stripe initiative for agent-driven purchasing, and OpenAI has since expanded ACP to support product discovery in ChatGPT.

Shopify's current UCP implementation includes merchant/business profiles, capability negotiation, Cart MCP and Checkout MCP, so these should be treated as existing ecosystem primitives rather than functionality we should reinvent.