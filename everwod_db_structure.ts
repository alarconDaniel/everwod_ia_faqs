// Masked pg_dump: use // pgdump: obfuscated | // pgdump: empty on fields. COPY output uses \N for NULL.
//
interface Accounting {
  amount: number;
  created_at: Date;
  date: Date;
  description: string;
  email: string; // pgdump: obfuscated
  financial_event_id: number;
  id: number;
  payment_method_id: number;
  type: string;
  updated_at: Date;
  workspace_id: number;
}

interface AgentChats {
  conversation: Record<string, any>;
  conversation_id: string;
  created_at: Date;
  id: string;
  model: string;
  phone: string; // pgdump: obfuscated
  thread: Record<string, any>;
  thread_id: string;
  updated_at: Date;
  workspace_id: number;
}

interface AgentFaqs {
  agent_id: string;
  answer: string;
  created_at: Date;
  deleted_at: Date;
  id: string;
  image: string; // pgdump: empty
  question: string;
  updated_at: Date;
}

interface AgentFiles {
  agent_id: string;
  created_at: Date;
  deleted_at: Date;
  file: string;
  id: string;
  model_file_id: string;
  original_name: string;
  updated_at: Date;
}

interface AgentMcps {
  agent_id: string;
  authorization: string; // pgdump: empty
  created_at: Date;
  id: string;
  integration: string;
  is_active: boolean;
  server_description: string; // pgdump: empty
  server_label: string; // pgdump: empty
  server_url: string; // pgdump: empty
  tools: Record<string, any>;
  updated_at: Date;
}

interface AgentRuns {
  created_at: Date;
  id: string;
  run: Record<string, any>;
  thread_id: string;
  updated_at: Date;
}

interface AgentTexts {
  agent_id: string;
  created_at: Date;
  deleted_at: Date;
  id: string;
  text: string;
  updated_at: Date;
}

interface Agents {
  created_at: Date;
  deleted_at: Date;
  id: string;
  model: string;
  model_vector_store_id: string;
  name: string;
  prompt: string;
  start_message: string;
  updated_at: Date;
  workspace_id: number;
}

interface Audiences {
  created_at: Date;
  description: string;
  file_path: string;
  id: string;
  name: string;
  rows: number;
  sample_rows: Record<string, any>;
  updated_at: Date;
  variables: Record<string, any>;
  workspace_id: number;
}

interface ChatMessages {
  agent_chat_id: string;
  created_at: Date;
  id: string;
  message: Record<string, any>;
  message_type: string;
  model_output: Record<string, any>; // pgdump: empty
  updated_at: Date;
}

interface ChatResponses {
  conversation_id: string;
  created_at: Date;
  id: string;
  response: Record<string, any>;
  updated_at: Date;
}

interface Events {
  amount: number;
  created_at: Date;
  deleted_at: Date;
  event_date: Date;
  id: number;
  member_id: number;
  note: string;
  plan_id: number;
  schedule_id: number;
  updated_at: Date;
  user_id: number;
  workspace_id: number;
}

interface FinancialEvents {
  created_at: Date;
  id: number;
  name: string;
  updated_at: Date;
}

interface LeadSchedule {
  created_at: Date;
  lead_id: number;
  schedule_date: Date;
  schedule_id: number;
  updated_at: Date;
}

interface Leads {
  birthday: Date; // pgdump: empty
  blood_type: string; // pgdump: empty
  consent: boolean;
  consent_channel: string;
  consent_date: Date; // pgdump: empty
  created_at: Date;
  email: string; // pgdump: obfuscated
  emergency_contact: string; // pgdump: empty
  experience_details: string; // pgdump: empty
  gender: string; // pgdump: empty
  health_preconditions: string; // pgdump: empty
  id: number;
  name: string; // pgdump: obfuscated
  phone: string; // pgdump: obfuscated
  updated_at: Date;
  workspace_id: number;
}

interface Mailboxes {
  created_at: Date;
  from: string; // pgdump: obfuscated
  id: number;
  message: string;
  updated_at: Date;
  workspace_id: number;
}

interface PaymentMethods {
  created_at: Date;
  deleted_at: Date;
  id: number;
  name: string;
  updated_at: Date;
}

interface PaymentOptions {
  active: boolean;
  created_at: Date;
  description: string;
  id: string;
  name: string;
  picture: string; // pgdump: empty
  updated_at: Date;
  workspace_id: number;
}

interface Payments {
  amount: number;
  created_at: Date;
  created_by_migration: boolean;
  deleted_at: Date;
  end_date: Date;
  id: number;
  note: string; // pgdump: empty
  payment_method_id: number;
  plan_id: number;
  product_id: string;
  service_id: string;
  start_date: Date;
  updated_at: Date;
  user_id: number;
}

interface PlanUser {
  created_at: Date;
  deleted_at: Date;
  plan_id: number;
  updated_at: Date;
  user_id: number;
  workspace_id: number;
}

interface Plans {
  created_at: Date;
  credits: number;
  credits_duration: number;
  credits_term: string;
  deleted_at: Date;
  description: string;
  duration: number;
  id: number;
  price: number;
  term_id: number;
  title: string;
  updated_at: Date;
  workspace_id: number;
}

interface ProductPictures {
  created_at: Date;
  id: string;
  picture: string; // pgdump: empty
  product_id: string;
  thumbnail: string; // pgdump: empty
  updated_at: Date;
}

interface Products {
  created_at: Date;
  deleted_at: Date;
  description: string;
  id: string;
  is_active: boolean;
  name: string; // pgdump: obfuscated
  price: number;
  updated_at: Date;
  workspace_id: number;
}

interface Roles {
  created_at: Date;
  default_dashboard: string;
  deleted_at: Date;
  id: number;
  name: string;
  updated_at: Date;
}

interface ScheduleConfigs {
  cancellation_limit: number;
  created_at: Date;
  id: string;
  reservation_limit: number;
  updated_at: Date;
  workspace_id: number;
}

interface Schedules {
  created_at: Date;
  deleted_at: Date;
  description: string;
  duration: number;
  id: number;
  limit_users: number;
  note: string;
  state: string;
  time: Date;
  title: string;
  updated_at: Date;
  week_day: string;
  workspace_id: number;
}

interface Services {
  amount: number;
  created_at: Date;
  deleted_at: Date;
  description: string; // pgdump: obfuscated
  duration: number;
  id: string;
  term_id: number;
  title: string; // pgdump: obfuscated
  updated_at: Date;
  workspace_id: number;
}

interface Terms {
  created_at: Date;
  deleted_at: Date;
  id: number;
  name: string;
  updated_at: Date;
}

interface UserSchedule {
  created_at: Date;
  device: string;
  schedule_date: Date;
  schedule_id: number;
  scheduled_by_admin: boolean;
  updated_at: Date;
  user_id: number;
}

interface UserWorkspace {
  created_at: Date;
  created_by_migration: boolean;
  deleted_at: Date;
  role_id: number;
  state: string;
  updated_at: Date;
  user_id: number;
  workspace_id: number;
}

interface Users {
  bio: string; // pgdump: empty
  birthday: Date; // pgdump: empty
  blood_type: string; // pgdump: empty
  completed_at: Date;
  created_at: Date;
  deleted_at: Date;
  document_number: string; // pgdump: empty
  document_type: string; // pgdump: empty
  email: string; // pgdump: obfuscated
  email_verified_at: Date; // pgdump: obfuscated
  emergency_phone: string; // pgdump: obfuscated
  emergency_phone_country_code: string;
  eps: string; // pgdump: empty
  experience_details: string; // pgdump: empty
  gender: string; // pgdump: empty
  goals: string; // pgdump: empty
  health_preconditions: string; // pgdump: empty
  height: number; // pgdump: empty
  id: number;
  name: string; // pgdump: obfuscated
  nickname: string; // pgdump: empty
  occupation: string; // pgdump: empty
  password: string; // pgdump: empty
  phone: string; // pgdump: obfuscated
  phone_country_code: string;
  picture: string; // pgdump: empty
  picture_thumbnail: string; // pgdump: empty
  remember_token: string; // pgdump: empty
  updated_at: Date;
}

interface WhatsappCampaigns {
  created_at: Date;
  ended_at: Date;
  id: string;
  started_at: Date;
  template_id: string;
  updated_at: Date;
  workspace_id: number;
}

interface WhatsappConfigs {
  access_token: string; // pgdump: empty
  business_pin: string; // pgdump: empty
  chat_close_after_seconds: number;
  code: string; // pgdump: empty
  created_at: Date;
  id: string;
  phone_number_id: string;
  status: string;
  updated_at: Date;
  waba_id: string; // pgdump: obfuscated
  workspace_id: number;
}

interface WhatsappTemplates {
  audience_id: string;
  body: string;
  buttons: Record<string, any>;
  category: string;
  created_at: Date;
  deleted_at: Date;
  footer: string;
  header: string;
  header_handle: string;
  header_resource: string;
  header_type: string;
  id: string;
  language: string;
  name: string;
  rejection_info: Record<string, any>;
  status: string;
  template_meta_id: string; // pgdump: obfuscated
  template_meta_name: string; // pgdump: obfuscated
  updated_at: Date;
  workspace_id: number;
}

interface WorkspaceConfigs {
  created_at: Date;
  id: number;
  updated_at: Date;
  user_id: number;
  workspace_id: number;
}

interface Workspaces {
  active: boolean;
  address: string; // pgdump: obfuscated
  address_details: string; // pgdump: obfuscated
  bio: string; // pgdump: empty
  category: string;
  created_at: Date;
  deleted_at: Date;
  email: string; // pgdump: obfuscated
  id: number;
  is_logo_active: boolean;
  latitude: number; // pgdump: empty
  logo: string;
  logo_thumbnail: string; // pgdump: empty
  longitude: number; // pgdump: empty
  name: string;
  nit: string; // pgdump: empty
  offer_plans: boolean;
  phone: string; // pgdump: obfuscated
  phone_country_code: string; // pgdump: empty
  updated_at: Date;
}