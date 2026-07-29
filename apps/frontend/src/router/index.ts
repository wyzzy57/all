import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
  type RouterHistory,
} from "vue-router";

import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/workbench" },
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/auth/LoginView.vue"),
  },
  {
    path: "/workbench",
    component: () => import("@/views/workbench/WorkbenchView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/model-space",
    component: () => import("@/views/model-space/ModelSpaceView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/data-preparation",
    component: () => import("@/views/data-preparation/DataPreparationView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/services",
    component: () => import("@/views/services/ServicesView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/services/:serviceId",
    component: () => import("@/views/services/ServicesView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/tasks",
    component: () => import("@/views/tasks/TasksView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/training-visualization",
    component: () => import("@/views/training-visualization/TrainingVisualizationView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/account",
    component: () => import("@/views/account/AccountView.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/admin/overview",
    component: () => import("@/views/admin/AdminOverviewView.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/users",
    component: () => import("@/views/admin/UserManagementView.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/groups",
    component: () => import("@/views/admin/GroupManagementView.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/authorization",
    component: () => import("@/views/admin/AuthorizationView.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/audit-logs",
    component: () => import("@/views/admin/AuditLogView.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/resources",
    component: () => import("@/views/resources/NodeManagementView.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
];

const DEFAULT_AUTHENTICATED_ROUTE = "/workbench";

function withoutTrailingSlash(path: string): string {
  return path.length > 1 ? path.replace(/\/+$/, "") : path;
}

export function safeRedirectTarget(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return DEFAULT_AUTHENTICATED_ROUTE;
  }

  try {
    const base = "http://visiox.local";
    const resolved = new URL(value, base);
    if (resolved.origin !== base || withoutTrailingSlash(resolved.pathname) === "/login") {
      return DEFAULT_AUTHENTICATED_ROUTE;
    }
    return `${resolved.pathname}${resolved.search}${resolved.hash}`;
  } catch {
    return DEFAULT_AUTHENTICATED_ROUTE;
  }
}

export function createAppRouter(
  history: RouterHistory = createWebHistory(),
  routeRecords: RouteRecordRaw[] = routes,
) {
  const router = createRouter({ history, routes: routeRecords });
  let sessionRestore: Promise<boolean> | null = null;

  function restoreSession(auth: ReturnType<typeof useAuthStore>): Promise<boolean> {
    if (sessionRestore) return sessionRestore;
    sessionRestore = (async () => {
      try {
        await auth.refresh();
        return auth.isAuthenticated;
      } catch {
        return false;
      }
    })().finally(() => {
      sessionRestore = null;
    });
    return sessionRestore;
  }

  router.beforeEach(async (to) => {
    const auth = useAuthStore();
    const wasInitialized = auth.initialized;
    if (!auth.initialized) await auth.initialize();

    const isLoginRoute = to.matched.some((record) => record.name === "login");
    if (isLoginRoute) {
      if (!auth.isAuthenticated) return true;
      return auth.user?.must_change_password
        ? "/account"
        : safeRedirectTarget(to.query.redirect);
    }
    const requiresAuth = Boolean(to.meta.requiresAuth || to.meta.requiresAdmin);
    if (requiresAuth && !auth.isAuthenticated && wasInitialized) {
      await restoreSession(auth);
    }
    if (requiresAuth && !auth.isAuthenticated) {
      return { path: "/login", query: { redirect: to.fullPath } };
    }
    if (
      auth.isAuthenticated
      && auth.user?.must_change_password
      && withoutTrailingSlash(to.path) !== "/account"
    ) {
      return "/account";
    }
    if (to.meta.requiresAdmin && !auth.isAdmin) return DEFAULT_AUTHENTICATED_ROUTE;
    return true;
  });

  return router;
}

export default createAppRouter();
