import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import {
  api,
  clearAccessToken,
  subscribeAuthSession,
  type AuthenticatedUser,
  type AuthOperation,
  type LoginResponse,
} from "@/api/client";

const MAX_TIMEOUT_MS = 2_147_483_647;

type StoreOperation = {
  generation: number;
  transportOperation: AuthOperation;
};

export const useAuthStore = defineStore("auth", () => {
  const user = ref<AuthenticatedUser | null>(null);
  const accessToken = ref<string | null>(null);
  const expiresAt = ref<number | null>(null);
  const initialized = ref(false);
  const loading = ref(false);
  const error = ref<string | null>(null);
  let expiryTimer: ReturnType<typeof setTimeout> | null = null;
  let operationGeneration = 0;
  let sessionIdentityGeneration = 0;
  let activeOperation: StoreOperation | null = null;

  const isAuthenticated = computed(() => Boolean(
    user.value && accessToken.value && expiresAt.value && expiresAt.value > Date.now(),
  ));
  const isAdmin = computed(() => user.value?.role === "admin");

  function cancelExpiryTimer(): void {
    if (expiryTimer !== null) clearTimeout(expiryTimer);
    expiryTimer = null;
  }

  function clearLocalSession(): void {
    cancelExpiryTimer();
    user.value = null;
    accessToken.value = null;
    expiresAt.value = null;
  }

  function scheduleExpiry(): void {
    cancelExpiryTimer();
    if (expiresAt.value === null) return;
    const remaining = expiresAt.value - Date.now();
    if (remaining <= 0) {
      clearSession();
      return;
    }
    expiryTimer = setTimeout(scheduleExpiry, Math.min(remaining, MAX_TIMEOUT_MS));
  }

  function applySession(response: LoginResponse): void {
    user.value = response.user;
    accessToken.value = response.access_token;
    expiresAt.value = Date.now() + response.expires_in * 1000;
    error.value = null;
    scheduleExpiry();
  }

  function clearSession(): void {
    clearAccessToken();
    clearLocalSession();
  }

  function beginOperation(): StoreOperation {
    const operation = {
      generation: ++operationGeneration,
      transportOperation: Symbol("auth-store-operation"),
    };
    activeOperation = operation;
    loading.value = true;
    error.value = null;
    return operation;
  }

  function isCurrent(operation: StoreOperation): boolean {
    return operationGeneration === operation.generation && activeOperation === operation;
  }

  function finishOperation(operation: StoreOperation): void {
    if (!isCurrent(operation)) return;
    activeOperation = null;
    loading.value = false;
  }

  const unsubscribe = subscribeAuthSession((session, event) => {
    const previousUserId = user.value?.id ?? null;
    const identityChanged = session === null || previousUserId !== session.user.id;
    const ownsEvent = activeOperation !== null
      && event.operations.has(activeOperation.transportOperation);
    if (!ownsEvent && identityChanged) {
      operationGeneration += 1;
      activeOperation = null;
      loading.value = false;
    }
    if (identityChanged) sessionIdentityGeneration += 1;
    if (session) applySession(session);
    else clearLocalSession();
    error.value = null;
  });

  onScopeDispose(() => {
    unsubscribe();
    cancelExpiryTimer();
  });

  async function initialize(): Promise<void> {
    if (initialized.value) return;
    const operation = beginOperation();
    try {
      const response = await api.refresh(operation.transportOperation);
      if (isCurrent(operation)) applySession(response);
    } catch (caught) {
      if (isCurrent(operation)) {
        clearLocalSession();
        // Initial refresh is a background session probe. Login errors belong to
        // explicit credential submission, not this silent bootstrap request.
        error.value = null;
      }
    } finally {
      if (isCurrent(operation)) initialized.value = true;
      finishOperation(operation);
    }
  }

  async function login(username: string, password: string): Promise<void> {
    const operation = beginOperation();
    try {
      const response = await api.login({ username, password }, operation.transportOperation);
      if (isCurrent(operation)) {
        applySession(response);
        initialized.value = true;
      }
    } catch (caught) {
      if (isCurrent(operation)) {
        clearLocalSession();
        error.value = errorMessage(caught);
      }
      throw caught;
    } finally {
      finishOperation(operation);
    }
  }

  async function refresh(): Promise<void> {
    const operation = beginOperation();
    try {
      const response = await api.refresh(operation.transportOperation);
      if (isCurrent(operation)) applySession(response);
    } catch (caught) {
      if (isCurrent(operation)) {
        clearLocalSession();
        error.value = errorMessage(caught);
      }
      throw caught;
    } finally {
      finishOperation(operation);
    }
  }

  async function logout(): Promise<void> {
    const operation = beginOperation();
    try {
      await api.logout(operation.transportOperation);
    } catch (caught) {
      if (isCurrent(operation)) error.value = errorMessage(caught);
      throw caught;
    } finally {
      if (isCurrent(operation)) clearLocalSession();
      finishOperation(operation);
    }
  }

  async function updateProfile(payload: { display_name?: string; email?: string }): Promise<AuthenticatedUser> {
    const operation = beginOperation();
    const expectedIdentityGeneration = sessionIdentityGeneration;
    const expectedUserId = user.value?.id ?? null;
    try {
      const updatedUser = await api.updateProfile(payload, operation.transportOperation);
      if (
        isCurrent(operation)
        && sessionIdentityGeneration === expectedIdentityGeneration
        && user.value?.id === expectedUserId
      ) {
        user.value = updatedUser;
      }
      return updatedUser;
    } catch (caught) {
      if (isCurrent(operation)) error.value = errorMessage(caught);
      throw caught;
    } finally {
      finishOperation(operation);
    }
  }

  async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
    const operation = beginOperation();
    try {
      await api.changePassword(
        { current_password: currentPassword, new_password: newPassword },
        operation.transportOperation,
      );
      if (isCurrent(operation)) clearLocalSession();
    } catch (caught) {
      if (isCurrent(operation)) error.value = errorMessage(caught);
      throw caught;
    } finally {
      finishOperation(operation);
    }
  }

  return {
    user,
    accessToken,
    expiresAt,
    initialized,
    loading,
    error,
    isAuthenticated,
    isAdmin,
    initialize,
    login,
    refresh,
    logout,
    updateProfile,
    changePassword,
    clearSession,
  };
});

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Authentication request failed";
}
