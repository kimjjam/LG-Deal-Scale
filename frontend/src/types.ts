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
  product?: string | null;
  quantity?: number | null;
  location?: string | null;
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
  created_at: string;
  updated_at: string;
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

export interface CsvImportResult {
  imported_count: number;
  errors: Array<{ row: number; error: string }>;
}

export interface CrmDashboard {
  pipeline: Record<OpportunityStage, { count: number; amount: number }>;
  weighted_amount: number;
  stage_probabilities: Record<OpportunityStage, number>;
  closed_conversion: { won: number; lost: number; denominator: number; rate: number | null; definition: string };
  tasks: { open: number; overdue: number };
  rep_stats: Array<{ staff_id: string; name: string; activity_count: number; opportunity_count: number; won_count: number; won_amount: number }>;
  average_stage_hours: Record<OpportunityStage, number | null>;
  ai_score_buckets: Array<{ range: string; scored_inquiries: number; closed_opportunities: number; won_opportunities: number; won_conversion: number | null }>;
}

export interface SearchResult {
  sql: string;
  rows: Array<Record<string, unknown>>;
}
