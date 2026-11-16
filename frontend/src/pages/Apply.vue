<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const clock = ref('')
const batches = ref([])
const strategy = ref('reject')
const msg = ref('')
const err = ref('')
const result = ref(null)
const detail = ref(null)
const detailBatch = ref(null)

async function refresh() {
  const c = await api.clock()
  clock.value = c.business_clock || ''
  strategy.value = c.drift_strategy || 'reject'
  batches.value = await api.batches()
}

async function saveClock() {
  msg.value = ''
  err.value = ''
  try {
    await api.setClock(clock.value)
    msg.value = '业务钟已更新'
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function changeStrategy() {
  msg.value = ''
  err.value = ''
  try {
    const r = await api.setStrategy(strategy.value)
    strategy.value = r.drift_strategy
    msg.value = `漂移策略已切换为「${strategy.value === 'reject' ? '遇漂移拒单' : '允许漂移应用'}」`
  } catch (e) {
    err.value = e.message
    await refresh()
  }
}

async function applyDue() {
  msg.value = ''
  err.value = ''
  result.value = null
  detail.value = null
  try {
    result.value = await api.applyDue()
    if (result.value.applied) {
      const r = result.value
      msg.value = `批次 ${r.batch_no}：应用 ${r.applied_count} 单，漂移拒绝 ${r.rejected_count} 单，漂移放行 ${r.drift_count} 行`
    } else {
      msg.value = result.value.message
    }
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function showLines(b) {
  err.value = ''
  try {
    detailBatch.value = b.batch_no
    detail.value = await api.batchLines(b.id)
  } catch (e) {
    err.value = e.message
  }
}

function decisionTag(d) {
  return d === 'applied' ? 'ok' : d === 'rejected' ? 'bad' : 'muted'
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>生效应用</h1>
    <p>拨动业务钟后，点「应用到期」将 effective_at ≤ 业务钟的改价单写入现行价签。应用每行前核对价签现行价与建单旧价，不一致即漂移；提交时还会再检测一次并发改写。</p>
  </section>

  <div class="panel">
    <h2>业务钟与漂移策略</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>业务钟 (UTC)</label>
        <input v-model="clock" />
      </div>
      <button class="btn ghost" type="button" @click="saveClock">保存业务钟</button>
      <button class="btn warn" type="button" @click="applyDue">应用到期</button>
    </div>
    <div class="row" style="margin-top:10px">
      <label class="radio">
        <input type="radio" value="reject" v-model="strategy" @change="changeStrategy" />
        遇漂移拒单（不写该单任何新价，单保持可处理态并记漂移原因）
      </label>
      <label class="radio">
        <input type="radio" value="allow" v-model="strategy" @change="changeStrategy" />
        允许漂移应用（写新价，历史与批次明细记期望/实际旧价与漂移标记）
      </label>
    </div>
    <p class="muted" style="margin:6px 0 0">策略互斥二选一；拒绝粒度固定为「单」——任一行漂移即整单拒绝，同批其他单不受影响。</p>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <div v-if="result && result.applied" class="panel" style="margin-top:12px;background:var(--bg-2)">
      <h2>本批结果（{{ result.batch_no }} · 策略 {{ result.strategy }}）</h2>
      <table>
        <thead>
          <tr><th>改价单</th><th>决定</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in result.tickets" :key="t.ticket_no">
            <td>{{ t.ticket_no }}</td>
            <td><span class="tag" :class="decisionTag(t.decision)">{{ t.decision }}</span></td>
            <td>{{ t.reason || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>应用批次</h2>
    <table>
      <thead>
        <tr><th>批次</th><th>业务钟</th><th>策略</th><th>到期单</th><th>已应用</th><th>漂移拒绝</th><th>漂移放行行</th><th>明细行</th><th>状态</th><th>时间</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td>
          <td>{{ b.clock_at }}</td>
          <td>{{ b.strategy || '—' }}</td>
          <td>{{ b.ticket_count }}</td>
          <td>{{ b.applied_count }}</td>
          <td>{{ b.rejected_count }}</td>
          <td>{{ b.drift_count }}</td>
          <td>{{ b.line_count }}</td>
          <td><span class="tag ok">{{ b.status }}</span></td>
          <td>{{ b.created_at }}</td>
          <td><button class="btn ghost" type="button" @click="showLines(b)">明细</button></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div v-if="detail" class="panel">
    <h2>批次明细（{{ detailBatch }}）：期望旧价 / 实际旧价 / 漂移标记</h2>
    <table>
      <thead>
        <tr><th>改价单</th><th>SKU</th><th>期望旧价</th><th>实际旧价</th><th>新价</th><th>漂移</th><th>再检测</th><th>决定</th><th>原因</th></tr>
      </thead>
      <tbody>
        <tr v-for="l in detail" :key="l.id">
          <td>{{ l.ticket_no }}</td>
          <td>{{ l.sku_code }}</td>
          <td>{{ l.expected_old_price }}</td>
          <td>{{ l.actual_old_price ?? '—' }}</td>
          <td>{{ l.new_price }}</td>
          <td><span v-if="l.drifted" class="tag warn">漂移</span><span v-else>—</span></td>
          <td><span v-if="l.rechecked" class="tag warn">并发改写</span><span v-else>—</span></td>
          <td><span class="tag" :class="decisionTag(l.decision)">{{ l.decision }}</span></td>
          <td>{{ l.reason || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
