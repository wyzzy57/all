import { defineStore } from "pinia";

import { api, type TaskRecord } from "@/api/client";

type State = {
  items: TaskRecord[];
  loading: boolean;
  error: string | null;
};

export const useTaskCenterStore = defineStore("taskCenter", {
  state: (): State => ({
    items: [],
    loading: false,
    error: null
  }),
  getters: {
    runningCount: (state) => state.items.filter((item) => item.status === "RUNNING").length,
    failedCount: (state) => state.items.filter((item) => item.status === "FAILED").length
  },
  actions: {
    async refresh() {
      this.loading = true;
      this.error = null;
      try {
        const response = await api.listTasks({ limit: 100 });
        this.items = response.items;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "任务加载失败";
      } finally {
        this.loading = false;
      }
    },
    async cancel(id: string) {
      await api.cancelTask(id);
      await this.refresh();
    }
  }
});
