<template>
  <section class="workbench-view">
    <header class="workbench-title">
      <h1>工作台</h1>
    </header>

    <section class="panel data-panel">
      <div class="panel-heading">
        <h2>数据准备分析</h2>
        <button type="button" class="quick-link" @click="goDataPreparation">快速访问›</button>
      </div>

      <div class="data-grid">
        <div class="data-chart-card">
          <div class="metric-strip">
            <div v-for="metric in dataMetrics" :key="metric.label">
              <span>{{ metric.label }}</span>
              <strong :style="{ color: metric.color }">{{ metric.value }}</strong>
            </div>
          </div>
          <div class="pie-row">
            <div class="pie-chart" aria-label="数据导入类型占比"></div>
            <ul class="pie-legend">
              <li v-for="metric in dataMetrics" :key="metric.label">
                <i :style="{ background: metric.color }"></i>
                <span>{{ metric.label }}</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="tag-card">
          <h3>数据准备标签</h3>
          <div v-for="tag in dataTags" :key="tag.name" class="progress-row">
            <span>{{ tag.name }}</span>
            <div class="progress-track">
              <i :style="{ width: `${tag.percent}%` }"></i>
            </div>
            <strong>{{ tag.count }}个</strong>
          </div>
        </div>

        <div class="dataset-list">
          <article v-for="dataset in datasets" :key="dataset.name" class="dataset-card">
            <h3>{{ dataset.name }}</h3>
            <div class="dataset-badges">
              <span class="success">已完成</span>
              <span class="imported">导入</span>
              <span>图像分类</span>
            </div>
            <p>{{ dataset.createdAt }} <a href="#">Label Studio</a></p>
          </article>
        </div>
      </div>
    </section>

    <div class="lower-grid">
      <section class="panel model-panel">
        <div class="panel-heading">
          <h2>模型空间分析</h2>
          <button type="button" class="quick-link" @click="goDataPreparation">快速访问›</button>
        </div>
        <div class="model-content">
          <div class="status-cards">
            <div v-for="status in modelStatuses" :key="status.label" class="status-card">
              <span :style="{ color: status.color }">●</span>
              <em>{{ status.label }}</em>
              <strong :style="{ color: status.color }">{{ status.value }}</strong>
            </div>
          </div>
          <div class="pipeline-bars">
            <div v-for="pipeline in pipelineStats" :key="pipeline.name" class="pipeline-row">
              <span>{{ pipeline.name }}</span>
              <div class="pipeline-track">
                <i :style="{ width: `${pipeline.percent}%` }"></i>
              </div>
              <strong>{{ pipeline.percent }}%</strong>
            </div>
          </div>
        </div>
      </section>

      <section class="panel services-panel">
        <div class="panel-heading">
          <h2>服务列表分析</h2>
          <button type="button" class="quick-link" @click="goServices">快速访问›</button>
        </div>
        <div class="service-table">
          <div v-for="service in services" :key="service.name" class="service-row">
            <span>{{ service.name }}</span>
            <time>{{ service.createdAt }}</time>
            <span v-if="service.pipeline">{{ service.pipeline }}</span>
            <strong :class="service.status">{{ service.statusText }}</strong>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";

const router = useRouter();

const dataMetrics = [
  { label: "已标注数据导入", value: 15, color: "#16a34a" },
  { label: "未标注数据导入", value: 35, color: "#8b35eb" },
  { label: "视频文件导入", value: 1, color: "#f59e0b" },
];

const dataTags = [
  { name: "labelstudio导入", count: 23, percent: 23 },
  { name: "关键点检测", count: 1, percent: 1 },
  { name: "图像分类", count: 4, percent: 4 },
  { name: "实例分割", count: 2, percent: 2 },
  { name: "目标检测", count: 22, percent: 22 },
];

const datasets = [
  { name: "xiaoanEval1", createdAt: "2026-06-02 06:37:14" },
  { name: "xiaoanEval", createdAt: "2026-06-02 06:04:59" },
  { name: "XiaoanDoor", createdAt: "2026-06-01 02:05:46" },
];

const modelStatuses = [
  { label: "运行中止", value: 30, color: "#dc2626" },
  { label: "运行成功", value: 37, color: "#16a34a" },
  { label: "配置中", value: 72, color: "#8b35eb" },
];

const pipelineStats = [
  { name: "PaddleOCR-VL训练", percent: 2 },
  { name: "偏好对齐", percent: 3 },
  { name: "图像分类", percent: 7 },
  { name: "大模型训练", percent: 10 },
];

const services = [
  { name: "千问3", createdAt: "2026-05-27 17:14:59", pipeline: "", status: "deploying", statusText: "部署中" },
  { name: "test_vllm_name1", createdAt: "2026-05-21 16:11:47", pipeline: "", status: "deploying", statusText: "部署中" },
  { name: "测试", createdAt: "2026-05-19 13:59:48", pipeline: "", status: "deploying", statusText: "部署中" },
  { name: "test_vllm_name", createdAt: "2026-05-19 13:17:00", pipeline: "", status: "deploying", statusText: "部署中" },
  { name: "test_divise", createdAt: "2026-04-24 14:03:30", pipeline: "huajiao123", status: "stopped", statusText: "已终止" },
];

function goDataPreparation() {
  void router.push("/data-preparation");
}

function goServices() {
  void router.push("/services");
}
</script>

<style scoped>
.workbench-view {
  min-height: 100%;
  background: #f5f7fb;
  color: #111827;
}

.workbench-title h1 {
  margin: 0 0 14px;
  font-size: 26px;
  font-weight: 600;
}

.panel {
  border: 1px solid #e3e8f0;
  background: #fff;
  box-shadow: 0 6px 16px rgb(15 23 42 / 4%);
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px 10px;
}

.panel-heading h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 500;
}

.quick-link {
  border: 0;
  background: transparent;
  color: #1763ff;
  cursor: pointer;
  font-size: 16px;
}

.data-grid {
  display: grid;
  grid-template-columns: minmax(480px, 1.45fr) minmax(320px, 0.95fr) minmax(280px, 0.62fr);
  gap: 18px;
  padding: 0 20px 12px;
}

.data-chart-card,
.tag-card,
.dataset-list {
  min-height: 412px;
  border: 1px solid #e1e6ee;
  background: #fff;
}

.metric-strip {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin: 10px;
  border: 1px solid #e4e8ef;
  background: #f8fafc;
}

.metric-strip div {
  display: grid;
  gap: 4px;
  justify-items: center;
  padding: 18px 8px 10px;
  font-size: 18px;
}

.metric-strip strong {
  font-size: 16px;
  font-weight: 500;
}

.pie-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 92px;
  min-height: 300px;
}

.pie-chart {
  width: 222px;
  height: 222px;
  border-radius: 50%;
  background: conic-gradient(#16a34a 0 29%, #8b35eb 29% 98%, #f59e0b 98% 100%);
}

.pie-legend {
  display: grid;
  gap: 30px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 16px;
}

.pie-legend li {
  display: flex;
  align-items: center;
  gap: 14px;
}

.pie-legend i {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.tag-card {
  padding: 24px 20px 0;
}

.tag-card h3 {
  margin: 0 0 12px;
  color: #1763ff;
  font-size: 16px;
  font-weight: 500;
}

.progress-row {
  display: grid;
  grid-template-columns: 112px 1fr 42px;
  align-items: center;
  gap: 10px;
  min-height: 74px;
  border-bottom: 1px solid #edf0f4;
  font-size: 16px;
}

.progress-track,
.pipeline-track {
  height: 8px;
  border-radius: 999px;
  background: #f1f2f4;
  overflow: hidden;
}

.progress-track i,
.pipeline-track i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: #2878ff;
}

.progress-row strong {
  color: #1763ff;
  font-weight: 500;
}

.dataset-list {
  display: grid;
  gap: 12px;
  padding: 12px 14px;
  overflow: hidden;
}

.dataset-card {
  border: 1px solid #dfe5ee;
  border-radius: 4px;
  padding: 22px 20px 14px;
}

.dataset-card h3 {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 600;
}

.dataset-badges {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.dataset-badges span,
.service-row strong {
  border-radius: 999px;
  padding: 6px 10px;
  background: #f2f4f7;
  font-size: 14px;
  font-weight: 500;
}

.dataset-badges .success {
  background: #dcfce7;
  color: #16a34a;
}

.dataset-badges .imported {
  background: #dcfce7;
  color: #16a34a;
}

.dataset-card p {
  margin: 0;
  color: #4b5563;
  font-size: 14px;
}

.dataset-card a {
  margin-left: 8px;
  color: #1763ff;
  text-decoration: none;
}

.lower-grid {
  display: grid;
  grid-template-columns: minmax(560px, 1fr) minmax(430px, 0.65fr);
  gap: 20px;
  margin-top: 20px;
}

.model-content {
  display: grid;
  grid-template-columns: 204px 1fr;
  gap: 48px;
  padding: 4px 20px 10px;
}

.status-cards {
  display: grid;
  gap: 12px;
}

.status-card {
  display: grid;
  grid-template-columns: 18px 1fr auto;
  align-items: center;
  min-height: 78px;
  padding: 0 20px;
  box-shadow: 0 6px 18px rgb(30 64 175 / 9%);
}

.status-card em {
  font-style: normal;
  font-size: 18px;
}

.status-card strong {
  font-size: 36px;
  font-weight: 400;
}

.pipeline-bars {
  display: grid;
  gap: 34px;
  padding: 8px 0 0;
}

.pipeline-row {
  display: grid;
  grid-template-columns: 140px 1fr 38px;
  align-items: center;
  gap: 14px;
  font-size: 15px;
}

.service-table {
  display: grid;
  gap: 6px;
  padding: 0 16px 16px;
}

.service-row {
  display: grid;
  grid-template-columns: minmax(130px, 1fr) 170px minmax(80px, 1fr) 74px;
  align-items: center;
  min-height: 50px;
  padding: 0 10px;
  background: #f8f5ff;
  font-size: 16px;
}

.service-row strong.deploying {
  background: #f1ddff;
  color: #8b35eb;
}

.service-row strong.stopped {
  background: #dbeafe;
  color: #1763ff;
}

@media (max-width: 1280px) {
  .data-grid,
  .lower-grid {
    grid-template-columns: 1fr;
  }
}
</style>
