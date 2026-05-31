/**
 * VidSOPEngine API Client - minimal portfolio build.
 * Surfaces only auth, video upload, SOP pipeline + i18n.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const PRESERVED_LOCAL_KEYS = new Set<string>([
  'vidsopengine.language',
  'vidsopengine.languageSelected',
  'vidsopengine.sidebarCollapsed',
  'vidsopengine.pwaInstallDismissed',
]);

function clearLocalAppState({ keepToken = false }: { keepToken?: boolean } = {}) {
  if (typeof window === 'undefined') return;
  const token = localStorage.getItem('token');
  const keysToRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    if (!key) continue;
    if (PRESERVED_LOCAL_KEYS.has(key)) continue;
    if (key.startsWith('vidsopengine.flag.')) continue;
    if (key === 'token' || key.startsWith('vidsopengine.')) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach((key) => localStorage.removeItem(key));
  if (keepToken && token) {
    localStorage.setItem('token', token);
  }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type VideoStatus = 'uploaded' | 'transcribing' | 'sop_generating' | 'completed' | 'failed';
export type VideoType = 'ui' | 'physical';
export type PipelineComplexity = 'auto' | 'procedural_complex' | 'atomic_simple';

export interface Video {
  id: string;
  title: string;
  filename: string;
  status: VideoStatus;
  video_type?: VideoType;
  pipeline_complexity?: PipelineComplexity;
  pipeline_complexity_confidence?: number | null;
  created_at: string;
  has_transcript: boolean;
  has_sop: boolean;
}

export interface Transcript {
  id: string;
  video_id: string;
  text: string;
  model_name: string;
  created_at: string;
}

export interface SOPStep {
  step_number: number;
  title: string;
  description: string;
  tools: string[];
  checks: string[];
  image_url?: string;
  evidence?: string[];
  confidence?: number;
  notes?: string;
  verified?: boolean | null;
  verification_quote?: string;
  correctness_score?: number | null;
  correctness_label?: string | null;
  correctness_reason?: string | null;
  correctness_issue_type?: string | null;
  user_marked_wrong?: boolean;
  user_correction_note?: string | null;
  user_correction_category?: string | null;
  warning?: string | null;
  estimated_time_minutes?: number | null;
  attachments?: Array<Record<string, unknown>>;
}

export interface SOP {
  title: string;
  description: string;
  steps: SOPStep[];
  notes: string[];
  overall_confidence?: number;
  warnings?: string[];
  needs_review?: boolean;
  video_type?: string | null;
  generation_metadata?: Record<string, unknown>;
  tools_materials?: string[];
  sections?: Array<Record<string, unknown>>;
  attachments?: Array<Record<string, unknown>>;
  source_type?: string;
}

export interface OperatorSOPStep {
  step_number: number;
  title: string;
  instruction: string;
  tools: string[];
  checks: string[];
  image_url?: string | null;
  notes?: string | null;
}

export interface OperatorSOP {
  title: string;
  description: string;
  steps: OperatorSOPStep[];
  tools: string[];
  warnings: string[];
  notes: string[];
}

export interface SOPResponse {
  id: string;
  video_id?: string | null;
  sop: SOP;
  operator_sop?: OperatorSOP | null;
  can_view_internal?: boolean;
  is_finalized: boolean;
  created_at: string;
  updated_at?: string | null;
  folder_id?: string | null;
  category?: string;
  tags?: string[];
  archived?: boolean;
  created_by?: string | null;
  updated_by?: string | null;
  visibility_scope?: 'private' | 'role' | 'team' | 'organization' | string;
  allowed_role_min?: 'staff' | 'manager' | 'admin' | string;
  shared_with_users?: string[];
  owner_email?: string | null;
  source_type?: 'ai_generated' | 'manual' | 'hybrid' | string;
  status?: 'draft' | 'published' | 'archived' | string;
  last_reviewed_at?: string | null;
  estimated_duration_minutes?: number | null;
  /** Always 0 in the minimal build — kept for UI compatibility. */
  linked_workflows_count?: number;
  linked_checklists_count?: number;
  linked_training_count?: number;
}

export interface SOPListResponse {
  sops: SOPResponse[];
  total: number;
}

export interface SOPFolder {
  id: string;
  name: string;
  parent_id?: string | null;
  owner_id: string;
  created_at: string;
  updated_at?: string | null;
}

export interface SOPFolderListResponse {
  folders: SOPFolder[];
  total: number;
}

export interface SOPManagementUpdatePayload {
  sop?: SOP;
  folder_id?: string | null;
  category?: string;
  tags?: string[];
  archived?: boolean;
  visibility_scope?: string;
  allowed_role_min?: string;
  shared_with_users?: string[];
  source_type?: string;
  status?: string;
  last_reviewed_at?: string | null;
  estimated_duration_minutes?: number | null;
}

export interface SOPABTestVariant {
  model: string;
  sop: SOP;
  step_scores: SOPStep[];
  overall_confidence: number;
  needs_review: boolean;
  warnings: string[];
}

export interface SOPABTestResponse {
  video_id: string;
  variants: SOPABTestVariant[];
}

export interface StatusResponse {
  video_id: string;
  status: VideoStatus;
  message?: string;
}

export interface JobResponse {
  job_id: string;
  video_id: string;
  status: string;
}

export interface User {
  id: string;
  email: string;
  created_at: string;
  role?: 'superadmin' | 'admin' | 'manager' | 'staff' | string;
  active?: boolean;
}

export interface RegisterPayload {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
  user?: User;
}

export interface ErrorResponse {
  error_code: string;
  message: string;
  details?: Record<string, unknown>;
}

class ApiError extends Error {
  constructor(
    public statusCode: number,
    public errorCode: string,
    message: string,
    public details?: Record<string, unknown>
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const message =
      errorData.message
      || (typeof errorData.detail === 'string' ? errorData.detail : null)
      || response.statusText;
    throw new ApiError(
      response.status,
      errorData.error_code || 'UNKNOWN_ERROR',
      message,
      errorData.details,
    );
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

function getAuthHeaders(): HeadersInit {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export const api = {
  // Auth
  async login(email: string, password: string): Promise<TokenResponse> {
    const response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, password }),
    });
    const data = await handleResponse<TokenResponse>(response);
    if (typeof window !== 'undefined') {
      clearLocalAppState();
      localStorage.setItem('token', data.access_token);
    }
    return data;
  },

  async register(payload: RegisterPayload): Promise<User> {
    const response = await fetch(`${API_BASE_URL}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return handleResponse<User>(response);
  },

  async refreshSession(): Promise<TokenResponse> {
    const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    });
    const data = await handleResponse<TokenResponse>(response);
    if (typeof window !== 'undefined') {
      localStorage.setItem('token', data.access_token);
    }
    return data;
  },

  async logoutSession(): Promise<void> {
    await fetch(`${API_BASE_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    }).catch(() => undefined);
    clearLocalAppState();
  },

  logout(): void {
    clearLocalAppState();
  },

  async getCurrentUser(): Promise<User> {
    const response = await fetch(`${API_BASE_URL}/auth/me`, { headers: getAuthHeaders() });
    return handleResponse<User>(response);
  },

  async forgotPassword(email: string): Promise<{ message: string; dev_otp?: string; retry_after_seconds?: number }> {
    const response = await fetch(`${API_BASE_URL}/auth/forgot-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    return handleResponse(response);
  },

  async verifyOtp(email: string, code: string): Promise<{ reset_token: string; expires_in_minutes: number }> {
    const response = await fetch(`${API_BASE_URL}/auth/verify-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, code }),
    });
    return handleResponse(response);
  },

  async resetPassword(resetToken: string, newPassword: string): Promise<{ ok: boolean; email: string }> {
    const response = await fetch(`${API_BASE_URL}/auth/reset-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reset_token: resetToken, new_password: newPassword }),
    });
    return handleResponse(response);
  },

  // Video upload + listing
  async uploadVideo(
    file: File,
    title: string | undefined,
    videoType: VideoType,
    onProgress?: (progress: number) => void,
    pipelineComplexity: PipelineComplexity = 'auto',
  ): Promise<Video> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('video_type', videoType);
    formData.append('pipeline_complexity', pipelineComplexity);
    if (title) formData.append('title', title);

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          try {
            const e = JSON.parse(xhr.responseText);
            reject(new ApiError(xhr.status, e.error_code || 'UPLOAD_FAILED', e.message || 'Upload failed', e.details));
          } catch {
            reject(new ApiError(xhr.status, 'UPLOAD_FAILED', 'Upload failed'));
          }
        }
      });
      xhr.addEventListener('error', () => reject(new ApiError(0, 'NETWORK_ERROR', 'Network error occurred')));
      xhr.open('POST', `${API_BASE_URL}/videos/upload`);
      const token = localStorage.getItem('token');
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      xhr.send(formData);
    });
  },

  async listVideos(): Promise<Video[]> {
    const response = await fetch(`${API_BASE_URL}/videos`, { headers: getAuthHeaders() });
    const data = await handleResponse<{ videos: Video[]; total: number }>(response);
    return data.videos;
  },

  async getVideo(id: string): Promise<Video> {
    const response = await fetch(`${API_BASE_URL}/videos/${id}`, { headers: getAuthHeaders() });
    return handleResponse<Video>(response);
  },

  async getVideoStatus(id: string): Promise<StatusResponse> {
    const response = await fetch(`${API_BASE_URL}/videos/${id}/status`, { headers: getAuthHeaders() });
    return handleResponse<StatusResponse>(response);
  },

  // Pipeline
  async runPipeline(videoId: string): Promise<JobResponse> {
    const response = await fetch(`${API_BASE_URL}/videos/${videoId}/pipeline/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    });
    return handleResponse<JobResponse>(response);
  },

  async getTranscript(videoId: string): Promise<Transcript> {
    const response = await fetch(`${API_BASE_URL}/videos/${videoId}/transcript`, { headers: getAuthHeaders() });
    return handleResponse<Transcript>(response);
  },

  // SOP (per-video, pipeline output)
  async getSOP(videoId: string): Promise<SOPResponse> {
    const response = await fetch(`${API_BASE_URL}/videos/${videoId}/sop`, { headers: getAuthHeaders() });
    return handleResponse<SOPResponse>(response);
  },

  async updateSOP(videoId: string, sop: SOP): Promise<SOPResponse> {
    const response = await fetch(`${API_BASE_URL}/videos/${videoId}/sop`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ sop }),
    });
    return handleResponse<SOPResponse>(response);
  },

  async finalizeSOP(videoId: string, sop: SOP): Promise<SOPResponse> {
    const response = await fetch(`${API_BASE_URL}/videos/${videoId}/sop/finalize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ sop }),
    });
    return handleResponse<SOPResponse>(response);
  },

  async translateSOP(videoId: string, targetLanguage: string): Promise<SOPResponse> {
    const response = await fetch(
      `${API_BASE_URL}/videos/${videoId}/sop/translate?target_language=${targetLanguage}`,
      { method: 'POST', headers: { ...getAuthHeaders() } },
    );
    return handleResponse<SOPResponse>(response);
  },

  async abTestSOPModels(videoId: string, models?: string[]): Promise<SOPABTestResponse> {
    const response = await fetch(`${API_BASE_URL}/videos/${videoId}/sop/ab-test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(models?.length ? { models } : {}),
    });
    return handleResponse<SOPABTestResponse>(response);
  },

  async markSOPAsCorrection(
    videoId: string,
    expectedSop: SOP,
    failureType: 'hallucination' | 'wrong_answer' | 'bad_formatting' | 'missing_step' | 'wrong_order' | 'low_confidence' | 'edge_case' = 'wrong_answer',
    severity: 'low' | 'medium' | 'high' = 'medium',
    notes?: string,
    actualSop?: SOP,
  ): Promise<unknown> {
    const response = await fetch(`${API_BASE_URL}/failures`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({
        video_id: videoId,
        expected_output: expectedSop,
        ...(actualSop ? { actual_output: actualSop } : {}),
        failure_type: failureType,
        severity,
        notes: notes || 'User-submitted correction from SOP viewer',
      }),
    });
    return handleResponse<unknown>(response);
  },

  // Managed SOP library
  async listManagedSOPs(filters: {
    search?: string;
    tag?: string;
    category?: string;
    folder_id?: string;
    archived?: boolean;
  } = {}): Promise<SOPListResponse> {
    const qs = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        qs.append(key, String(value));
      }
    });
    const url = qs.toString() ? `${API_BASE_URL}/sops?${qs}` : `${API_BASE_URL}/sops`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse<SOPListResponse>(response);
  },

  async getManagedSOP(sopId: string): Promise<SOPResponse> {
    const response = await fetch(`${API_BASE_URL}/sops/${sopId}`, { headers: getAuthHeaders() });
    return handleResponse<SOPResponse>(response);
  },

  async createManagedSOP(payload: SOPManagementUpdatePayload & { sop: SOP }): Promise<SOPResponse> {
    const response = await fetch(`${API_BASE_URL}/sops`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(payload),
    });
    return handleResponse<SOPResponse>(response);
  },

  async updateManagedSOP(sopId: string, payload: SOPManagementUpdatePayload): Promise<SOPResponse> {
    const response = await fetch(`${API_BASE_URL}/sops/${sopId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(payload),
    });
    return handleResponse<SOPResponse>(response);
  },

  async archiveManagedSOP(sopId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/sops/${sopId}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    });
    await handleResponse<void>(response);
  },

  async publishManagedSOP(sopId: string): Promise<SOPResponse> {
    const response = await fetch(`${API_BASE_URL}/sops/${sopId}/publish`, {
      method: 'POST',
      headers: getAuthHeaders(),
    });
    return handleResponse<SOPResponse>(response);
  },

  // SOP folders
  async listSOPFolders(): Promise<SOPFolderListResponse> {
    const response = await fetch(`${API_BASE_URL}/sop-folders`, { headers: getAuthHeaders() });
    return handleResponse<SOPFolderListResponse>(response);
  },

  async createSOPFolder(name: string, parentId?: string | null): Promise<SOPFolder> {
    const response = await fetch(`${API_BASE_URL}/sop-folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ name, parent_id: parentId || null }),
    });
    return handleResponse<SOPFolder>(response);
  },

  async updateSOPFolder(folderId: string, payload: { name?: string; parent_id?: string | null }): Promise<SOPFolder> {
    const response = await fetch(`${API_BASE_URL}/sop-folders/${folderId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(payload),
    });
    return handleResponse<SOPFolder>(response);
  },

  async deleteSOPFolder(folderId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/sop-folders/${folderId}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    });
    await handleResponse<void>(response);
  },

  // Step image upload (used by SOPEditor)
  async uploadSOPStepImage(file: File): Promise<{ image_url: string; filename: string }> {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch(`${API_BASE_URL}/sops/step-images`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: form,
    });
    return handleResponse<{ image_url: string; filename: string }>(response);
  },

  // i18n
  async listLanguages(): Promise<{ languages: Array<{ code: string; label: string; native: string; iso: string }> }> {
    const response = await fetch(`${API_BASE_URL}/auth/languages`);
    return handleResponse(response);
  },
};

export { ApiError };
export default api;
