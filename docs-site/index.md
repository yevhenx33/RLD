---
layout: page
title: RLD Protocol
---

<script setup>
import { onMounted } from 'vue'
import { useRouter, useData } from 'vitepress'

onMounted(() => {
  const router = useRouter()
  const { site } = useData()
  router.go(site.value.base + 'introduction/rate-level-derivatives')
})
</script>

<meta http-equiv="refresh" content="0; url=/docs/introduction/rate-level-derivatives">
