import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/model-space" },
    {
      path: "/model-space",
      component: () => import("@/views/model-space/ModelSpaceView.vue")
    },
    {
      path: "/data-preparation",
      component: () => import("@/views/data-preparation/DataPreparationView.vue")
    },
    {
      path: "/pipelines",
      component: () => import("@/views/pipelines/PipelinesView.vue")
    },
    {
      path: "/tasks",
      component: () => import("@/views/tasks/TasksView.vue")
    },
    {
      path: "/devices",
      component: () => import("@/views/devices/DevicesView.vue")
    },
    {
      path: "/edge-apps",
      component: () => import("@/views/edge-apps/EdgeAppsView.vue")
    },
    {
      path: "/deployments",
      component: () => import("@/views/devices/DeploymentsView.vue")
    }
  ]
});

export default router;
