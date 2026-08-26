export type Role = "owner" | "manager" | "rep";
export type InquiryStatus = "open" | "routed" | "resolved";
export type IntentCategory = "구매임박" | "정보탐색" | "AS·불만";
export type OpportunityStage = "qualify" | "develop" | "propose" | "won" | "lost";
export type TaskStatus = "pending" | "completed";
export type ActivityType = "call" | "email" | "meeting" | "note" | "purchase";

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
  seat_count?: number | null;
  employee_count?: number | null;
  store_count?: number | null;
  product?: string | null;
  quantity?: number | null;
  location?: string | null;
  purchase_stage?: "견적 요청" | "모델 비교" | "정보 수집" | null;
  purchase_timing?: "즉시" | "1개월 이내" | "3개월 이내" | "미정" | null;
}

export interface ChatTurn {
  message: string;
  fields: IntakeFields;
  ready_for_analysis: boolean;
  returning_customer: boolean;
}

export interface ProductRecommendation {
  name: string;
  brand: string;
  price: number | null;
  price_label: string;
  price_source_url?: string | null;
  price_verified_at?: string | null;
  usage_label?: string | null;
  product_url: string;
}

export type NearbyStoreStatus = "location_missing" | "not_configured" | "failed" | "no_results" | "success";

export interface NearbyStoreSearch {
  status: NearbyStoreStatus;
  message: string;
  stores: Array<{ name: string; address: string; phone: string }>;
}

export interface PublicResult {
  inquiry_id: number;
  confirmation: string;
  analysis?: string | null;
  analysis_error: boolean;
  products: ProductRecommendation[];
  stores: Array<{ name: string; address: string; phone: string }>;
  nearby_store_status: NearbyStoreStatus;
  nearby_store_message: string;
  regional_team_connected: boolean;
  partner?: {
    name: string;
    address: string;
    phone: string | null;
    partner_type: string;
    verified_at: string;
  } | null;
}

export interface InquiryScore {
  fit: number;
  intent: number;
  recency: number;
  total: number;
  category: IntentCategory;
  confidence: number;
  reasoning: Record<"fit" | "intent" | "recency", string>;
}

export interface Inquiry {
  id: number;
  account_id: number;
  account_name?: string | null;
  content: string;
  channel?: string;
  status: InquiryStatus;
  created_at: string;
  assignee_id?: string | null;
  assignee_name?: string | null;
  routing_manager_id?: string | null;
  routing_manager_name?: string | null;
  partner?: Partner | null;
  score?: InquiryScore | null;
  nearby_store_search?: NearbyStoreSearch | null;
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
  is_active: boolean;
}

export interface Contact {
  id: number;
  account_id: number;
  name: string;
  role: string | null;
  phone: string | null;
  email: string | null;
}

export interface Opportunity {
  id: number;
  account_id: number;
  assignee_id: string;
  title: string;
  inquiry_id: number | null;
  lead_id: number | null;
  amount: string | number | null;
  probability: number;
  expected_close_date: string | null;
  stage: OpportunityStage;
  loss_reason: string | null;
  items: OpportunityItem[];
  items_total: string | number;
  created_at: string;
  updated_at: string;
}

export interface OpportunityItem {
  id: number;
  opportunity_id: number;
  product_id: number | null;
  product_name: string;
  quantity: number;
  unit_price: string | number;
}

export interface OpportunityPatch {
  expected_updated_at: string;
  assignee_id?: string;
  title?: string;
  amount?: string | number | null;
  probability?: number;
  expected_close_date?: string | null;
  stage?: OpportunityStage;
  loss_reason?: string | null;
  items: Array<Omit<OpportunityItem, "id" | "opportunity_id"> & { id: number | null }>;
}

export interface VerifiedProduct {
  id: number;
  name: string;
  brand: string;
  category: string;
  price: string | number;
}

export interface Activity {
  id: number;
  account_id: number;
  type: ActivityType;
  staff_id: string | null;
  contact_id: number | null;
  inquiry_id: number | null;
  opportunity_id: number | null;
  content: string | null;
  outcome: string | null;
  amount: string | number | null;
  created_at: string;
}

export interface Task {
  id: number;
  account_id: number;
  assignee_id: string;
  title: string;
  due_at: string;
  opportunity_id: number | null;
  inquiry_id: number | null;
  status: TaskStatus;
  completed_at: string | null;
  created_at: string;
}

export interface TimelineItem {
  kind: "inquiry" | "activity" | "opportunity" | "task";
  id: number;
  at: string;
  text: string;
}

export interface AccountOverview {
  account: Account;
  contacts: Contact[];
  inquiries: Inquiry[];
  activities: Activity[];
  opportunities: Opportunity[];
  tasks: Task[];
  timeline: TimelineItem[];
}

export interface AccountDataQuality {
  duplicate_contacts: Array<{ field: "phone" | "email"; value: string; contact_ids: number[] }>;
}

export interface InquiryStatusResult {
  id: number;
  account_id: number;
  channel: string;
  content: string;
  status: InquiryStatus;
  created_at: string;
}

export interface IntentCorrectionResult {
  inquiry_id: number;
  category: IntentCategory;
  intent_score: number;
  total_score: number;
}

export interface AssignmentResult {
  assignment_id: number;
  status: InquiryStatus;
}

export interface Partner {
  id: number;
  name: string;
  address: string;
  phone: string | null;
  region: string;
  partner_type: string;
  verification_source: string;
  verified_at: string;
  is_active: boolean;
}

export interface SalesRegion {
  id: number;
  region_name: string;
  match_keyword: string;
  manager_id: string;
  manager_name: string;
  is_active: boolean;
}

export interface Lead {
  id: number;
  name: string;
  address?: string | null;
  business_type?: string | null;
  assignee_id?: string | null;
  assignee_name?: string | null;
  contact_name?: string | null;
  contact_phone?: string | null;
  contact_email?: string | null;
  next_action_at?: string | null;
  source: string;
  lead_score: number;
  reasoning: Record<string, string>;
  evidence?: {
    permit_status?: string;
    official_permits?: Array<{ kind?: string | null; date?: string | null; building_name?: string | null }>;
    naver_status?: string;
    online_mentions?: Array<{ source: string; title: string; link: string; published_at?: string | null }>;
    checked_at?: string;
  };
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

export interface CsvImportResult {
  imported_count: number;
  errors: Array<{ row: number; error: string }>;
}

export interface CrmDashboard {
  pipeline: Record<OpportunityStage, { count: number; amount: number }>;
  weighted_amount: number;
  stage_probabilities: Record<OpportunityStage, number>;
  closed_conversion: { won: number; lost: number; denominator: number; rate: number | null; definition: string };
  tasks: { open: number; overdue: number; due_today: number };
  forecast: { months: Array<{ month: string; count: number; amount: number; weighted_amount: number }>; missing_close_date: number };
  rep_stats: Array<{ staff_id: string; name: string; activity_count: number; opportunity_count: number; won_count: number; won_amount: number }>;
  average_stage_hours: Record<OpportunityStage, number | null>;
  ai_score_buckets: Array<{ range: string; scored_inquiries: number; closed_opportunities: number; won_opportunities: number; won_conversion: number | null }>;
}

export interface SearchResult {
  sql: string;
  rows: Array<Record<string, unknown>>;
}
