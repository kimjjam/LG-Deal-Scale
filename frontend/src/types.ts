export type Role = "manager" | "rep";

export interface Session {
  accessToken: string;
  role: Role;
  name: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface IntakeFields {
  business_name?: string | null;
  phone?: string | null;
  inquiry?: string | null;
  business_type?: string | null;
  room_count?: number | null;
  product?: string | null;
  quantity?: number | null;
  location?: string | null;
}

export interface ChatTurn {
  message: string;
  fields: IntakeFields;
  ready_for_analysis: boolean;
  returning_business_name?: string | null;
}

export interface ProductRecommendation {
  name: string;
  brand: string;
  price: number;
  product_url: string;
}

export interface PublicResult {
  inquiry_id: number;
  confirmation: string;
  analysis?: string | null;
  analysis_error: boolean;
  products: ProductRecommendation[];
  stores: Array<{ name: string; address: string; phone: string }>;
}

export interface InquiryScore {
  fit: number;
  intent: number;
  recency: number;
  total: number;
  category: string;
  confidence: number;
  reasoning: Record<"fit" | "intent" | "recency", string>;
}

export interface Inquiry {
  id: number;
  account_id: number;
  account_name?: string | null;
  content: string;
  status: string;
  created_at: string;
  assignee_id?: string | null;
  assignee_name?: string | null;
  score?: InquiryScore | null;
}

export interface Account {
  id: number;
  name: string;
  phone: string;
  attributes: Record<string, unknown>;
  created_at: string;
}

export interface StaffMember {
  id: string;
  name: string;
  email: string;
  role: Role;
}

export interface Lead {
  id: number;
  name: string;
  address?: string | null;
  business_type?: string | null;
  lead_score: number;
  reasoning: Record<string, string>;
  pipeline_stage: string;
}

export interface OutboundDashboard {
  pipeline: Record<string, number>;
  draft_approval_rate: number;
  sequence_distribution: Record<string, number>;
  outbound_email_mode: "dry_run" | "test_override";
}

export interface OutboundDraft {
  id: number;
  lead_id: number;
  sequence_step: number;
  subject: string;
  body: string;
  generated_at: string;
  reviewed: boolean;
  send_mode: "dry_run" | "test_override";
  sent_at?: string | null;
}

export interface SearchResult {
  sql: string;
  rows: Array<Record<string, unknown>>;
}
