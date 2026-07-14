import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/workbench" },
    {
      path: "/workbench",
      component: () => import("@/views/workbench/WorkbenchView.vue")
    },
    {
      path: "/model-space",
      component: () => import("@/views/model-space/ModelSpaceView.vue")
    },
    {
      path: "/data-preparation",
      component: () => import("@/views/data-preparation/DataPreparationView.vue")
    },
    {
      path: "/services",
      component: () => import("@/views/services/ServicesView.vue")
    },
    {
      path: "/services/:serviceId",
      component: () => import("@/views/services/ServicesView.vue")
    },
    {
      path: "/tasks",
      component: () => import("@/views/tasks/TasksView.vue")
    },
    {
      path: "/training-visualization",
      component: () => import("@/views/training-visualization/TrainingVisualizationView.vue")
    }
  ]
});

export default router;
