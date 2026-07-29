<template>
  <section class="pipeline-wizard-shell">
    <header class="wizard-topbar">
      <button class="back-link" type="button" @click="$emit('back')">
        <el-icon><ArrowLeft /></el-icon>
        返回产线列表
      </button>
      <nav class="wizard-steps" aria-label="产线配置步骤">
        <button
          v-for="(step, index) in steps"
          :key="step"
          type="button"
          :class="{ active: activeStep === index, done: activeStep > index }"
          :aria-current="activeStep === index ? 'step' : undefined"
          @click="$emit('step', index)"
        >
          <span>{{ index + 1 }}</span>
          <strong>{{ step }}</strong>
        </button>
      </nav>
    </header>

    <section class="wizard-panel">
      <slot />
      <footer class="wizard-footer">
        <span v-if="draftStatus" class="draft-status" :class="draftStatusTone" role="status">{{ draftStatus }}</span>
        <el-button v-if="activeStep > 0" plain @click="$emit('previous')">上一步</el-button>
        <el-button v-if="showDirectDeploy && activeStep === 0" plain @click="$emit('directDeploy')">直接部署</el-button>
        <el-button v-if="showSaveDraft" plain :loading="draftSaving" @click="$emit('saveDraft')">保存草稿</el-button>
        <el-button v-if="activeStep < steps.length - 1" type="primary" @click="$emit('next')">下一步</el-button>
        <el-button v-else type="primary" :loading="submitting" @click="$emit('submit')">提交训练</el-button>
      </footer>
    </section>
  </section>
</template>

<script setup lang="ts">
import { ArrowLeft } from "@element-plus/icons-vue";
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    steps: string[];
    activeStep: number;
    submitting?: boolean;
    showDirectDeploy?: boolean;
    showSaveDraft?: boolean;
    draftSaving?: boolean;
    draftStatus?: string;
  }>(),
  {
    submitting: false,
    showDirectDeploy: false,
    showSaveDraft: false,
    draftSaving: false,
    draftStatus: "",
  },
);

defineEmits<{
  back: [];
  step: [index: number];
  previous: [];
  next: [];
  submit: [];
  saveDraft: [];
  directDeploy: [];
}>();

const draftStatusTone = computed(() => ({
  saving: props.draftSaving,
  error: props.draftStatus.includes("失败"),
  saved: !props.draftSaving && !props.draftStatus.includes("失败"),
}));
</script>

<style scoped>
.pipeline-wizard-shell { width: min(100%, 1440px); margin: 0 auto; }
.wizard-topbar { position: relative; display: flex; min-height: 74px; align-items: flex-start; justify-content: center; padding-top: 8px; }
.back-link { position: absolute; top: 10px; left: 0; display: inline-flex; align-items: center; gap: 6px; border: 0; background: transparent; color: #1763ff; cursor: pointer; }
.wizard-steps { display: grid; width: min(640px, 56vw); margin: 0 auto; grid-template-columns: repeat(4, 1fr); }
.wizard-steps button { position: relative; display: grid; justify-items: center; gap: 8px; padding: 0; border: 0; background: transparent; color: #98a2b3; cursor: pointer; font-size: 14px; }
.wizard-steps button::before { position: absolute; top: 10px; left: calc(-50% + 12px); width: calc(100% - 24px); height: 2px; background: #98a2b3; content: ""; }
.wizard-steps button:first-child::before { display: none; }
.wizard-steps span { position: relative; z-index: 1; display: inline-flex; width: 24px; height: 24px; align-items: center; justify-content: center; border: 2px solid #98a2b3; border-radius: 50%; background: #fff; color: #667085; font-weight: 700; line-height: 1; }
.wizard-steps strong { font-size: 15px; font-weight: 500; }
.wizard-steps button.active, .wizard-steps button.done { color: #111827; }
.wizard-steps button.active span, .wizard-steps button.done span { border-color: #111827; color: #111827; }
.wizard-steps button.done::before, .wizard-steps button.active::before { background: #111827; }
.wizard-panel { min-height: 620px; padding: 28px 32px; border: 1px solid #edf0f5; background: #fff; }
.wizard-footer { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 28px; }
.draft-status { margin-right: auto; color: #667085; font-size: 13px; }
.draft-status.saved { color: #027a48; }
.draft-status.error { color: #b42318; }

@media (max-width: 820px) {
  .wizard-topbar { display: grid; gap: 14px; }
  .back-link { position: static; justify-self: start; }
  .wizard-steps { width: 100%; }
  .wizard-steps strong { font-size: 12px; }
  .wizard-panel { padding: 22px 18px; }
  .wizard-footer { position: sticky; bottom: 0; z-index: 4; margin: 24px -18px -22px; padding: 12px 18px; border-top: 1px solid #e4e7ec; background: #fff; }
}

@media (max-width: 560px) {
  .wizard-steps strong { max-width: 56px; text-align: center; }
  .draft-status { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
}
</style>
