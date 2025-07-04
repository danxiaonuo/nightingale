# Nightingale 事件聚合功能改造说明

## 1. 需求背景

为避免同一类告警事件在短时间内频繁通知，需实现**基于 rule_name 的事件聚合**，即：
**1分钟内同一 rule_name（及通知参数一致）的事件不单独发送，而是聚合后批量发送。**

---

## 2. 主要改动点

### 2.1 新增聚合缓存与锁
在 `Dispatch` 结构体中，新增如下字段：

```go
aggrCache map[string]*AggregatedEvents // 聚合缓存
aggrLock  sync.Mutex                  // 互斥锁
```

并在 `NewDispatch` 构造函数中初始化：

```go
aggrCache: make(map[string]*AggregatedEvents),
```

### 2.2 定义聚合Key结构体
新增 `AggregationKey` 结构体，作为聚合的唯一标识，包含以下字段：
- RuleName（告警名称）
- NotifyRuleId
- NotifyConfigChannelId
- NotifyConfigTemplateId
- NotifyChannelId
- MessageTemplateId

并实现 `String()` 方法，用于唯一标识每组聚合：

```go
type AggregationKey struct {
    RuleName          string
    NotifyRuleId      int64
    NotifyConfigChannelId    int64
    NotifyConfigTemplateId   int64
    NotifyChannelId   int64
    MessageTemplateId int64
}
func (k AggregationKey) String() string {
    return fmt.Sprintf("%s|%d|%d|%d|%d|%d", k.RuleName, k.NotifyRuleId, k.NotifyConfigChannelId, k.NotifyConfigTemplateId, k.NotifyChannelId, k.MessageTemplateId)
}
```

### 2.3 聚合事件结构体扩展
`AggregatedEvents` 结构体用于存储同一组聚合事件及其相关参数：

```go
type AggregatedEvents struct {
    Events          []*models.AlertCurEvent
    FirstTime       int64
    NotifyRuleId      int64
    NotifyConfig      *models.NotifyConfig
    NotifyChannel     *models.NotifyChannelConfig
    MessageTemplate   *models.MessageTemplate
}
```

### 2.4 修改事件处理逻辑
在 `HandleEventWithNotifyRule` 方法中，
原本每个事件直接发送，现改为调用 `AddToAggregation`，将事件按聚合key缓存，不立即发送。

### 2.5 新增定时批量发送协程
新增 `StartAggregationSender` 方法，启动一个定时协程，每10秒检查一次聚合缓存。
如果某组事件已聚合满1分钟，则批量发送（调用 `sendV2`），并清理缓存。

```go
func (e *Dispatch) StartAggregationSender() {
    go func() {
        for {
            time.Sleep(10 * time.Second)
            now := time.Now().Unix()
            e.aggrLock.Lock()
            for key, aggr := range e.aggrCache {
                if now-aggr.FirstTime >= 60 && len(aggr.Events) > 0 {
                    go e.sendV2(aggr.Events, aggr.NotifyRuleId, aggr.NotifyConfig, aggr.NotifyChannel, aggr.MessageTemplate)
                    delete(e.aggrCache, key)
                }
            }
            e.aggrLock.Unlock()
        }
    }()
}
```

### 2.6 保证参数不一致时分别聚合
通过聚合key的唯一性，保证只有参数完全一致的事件才会被聚合在一起，参数不同的事件会分别聚合、分别发送，避免混淆。

---

## 3. 聚合实现原理

- 事件到达时，按聚合key缓存到 `aggrCache`，不立即发送。
- 定时协程每10秒检查一次缓存，若某组事件已满1分钟，则批量发送。
- 发送后及时清理缓存，防止内存泄漏。
- 通过 key 结构体保证参数一致性，避免不同通知参数的事件混合。

---

## 4. 影响范围
- 事件通知发送逻辑（`alert/dispatch/dispatch.go`）
- 事件聚合与批量发送功能
- 相关结构体和方法的扩展

---

## 5. 配置与扩展
- **聚合时间**：目前为60秒（1分钟），可通过修改 `StartAggregationSender` 中的 `60` 调整。
- **聚合维度**：如需更细粒度聚合，可扩展 `AggregationKey` 字段。
- **检查周期**：可调整 `time.Sleep(10 * time.Second)` 的周期。

---

## 6. 总结

本次改造实现了"同一rule_name且参数一致的事件，1分钟内聚合批量发送"的功能，
有效减少了重复告警通知，提升了运维体验和系统可控性。

如需进一步扩展聚合逻辑，可根据实际需求调整聚合key和聚合周期。 
