<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue";
defineProps<{ title: string }>();
const emit = defineEmits<{ close: [] }>();
const el = ref<HTMLElement>();
let previous: HTMLElement | null = null;
function key(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
  if (e.key === "Tab") {
    const nodes = el.value?.querySelectorAll<HTMLElement>(
      "button,input,select,textarea,a[href]",
    );
    if (!nodes?.length) return;
    const first = nodes[0],
      last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
}
onMounted(() => {
  previous = document.activeElement as HTMLElement;
  el.value?.querySelector("button")?.focus();
  document.addEventListener("keydown", key);
});
onBeforeUnmount(() => {
  document.removeEventListener("keydown", key);
  previous?.focus();
});
</script>
<template>
  <div class="scrim" @click.self="emit('close')">
    <section
      ref="el"
      class="drawer"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
    >
      <header>
        <h2>{{ title }}</h2>
        <button aria-label="상세 닫기" @click="emit('close')">닫기 ×</button>
      </header>
      <slot />
    </section>
  </div>
</template>
