export interface MerchantSummary {
  id: string
  name: string
  status: string
  category: string | null
  website_url: string | null
}

export interface ProfilePolicies {
  max_auto_purchase_minor: number
  approval_threshold_minor: number
  allowed_categories: string[]
  currency: string
  return_window_days: number
  allow_cancellation: boolean
}

export interface Profile {
  profile_version: number
  merchant: {
    id: string
    name: string
    description: string | null
    category: string | null
    subcategories: string[]
    website: string | null
    logo_url: string | null
  }
  commerce: { currency: string; capabilities: string[] }
  policies: ProfilePolicies
  source: { provider: string; store_url: string | null; last_synced_at: string | null }
  storefront_status?: string
  sync?: { provider: string | null; last_synced_at: string | null; product_count: number }
}

export interface PoliciesPayload extends ProfilePolicies {
  version: number
}

export interface DemoVariant {
  external_id: string
  product_title: string | null
  title: string | null
  options: Record<string, string>
  price_minor: number
  available_for_sale: boolean
  available_quantity: number | null
}

export interface AgentEventPayload {
  type: 'status' | 'tool_call' | 'tool_result' | 'final' | 'error'
  tool: string | null
  label: string
  payload: Record<string, unknown> & {
    result?: {
      blocked?: boolean
      status?: string
      transaction_id?: string
      reason_codes?: string[]
      explanation?: string
      payment_initiation?: PaymentInitiation
      quote_id?: string
      total_minor?: number
      currency?: string
    }
    outcome?: string
    scenario?: string
  }
}

export interface PaymentInitiation {
  provider: string
  order_id: string
  amount_minor: number
  currency: string
  txn_ref: string
  key_id?: string
}

export interface TxnEvent {
  id: number
  event_type: string
  actor: string
  timestamp: string
  payload: Record<string, unknown>
}

export interface TransactionTrace {
  transaction_id: string
  session_id: string | null
  status: string
  requested_amount_minor: number | null
  quoted_amount_minor: number | null
  authorized_amount_minor: number | null
  final_amount_minor: number | null
  currency: string
  shopify_reference: string | null
  razorpay_order_id: string | null
  razorpay_payment_id: string | null
  created_at: string | null
  updated_at: string | null
}
