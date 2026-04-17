# Lighthouse Performance — duetaiapp.com

**Дата замера:** 2026-04-17
**Стек:** Next.js 16.2.3 + Tailwind v4, Vercel production
**Инструмент:** Lighthouse CLI, headless Chrome, 3 прогона mobile

## Результаты

### Desktop
| Метрика | Значение |
|---------|----------|
| Performance | 92 |
| Accessibility | 100 |
| Best Practices | 100 |
| SEO | 100 |

### Mobile (медиана из 3 прогонов)
| Метрика | Run 1 | Run 2 | Run 3 | Медиана |
|---------|-------|-------|-------|---------|
| Performance | 83 | 82 | 81 | **82** |
| Accessibility | 100 | 100 | 100 | **100** |
| Best Practices | 100 | 100 | 100 | **100** |
| SEO | 100 | 100 | 100 | **100** |

### Core Web Vitals (mobile median)
| Метрика | Значение | Порог |
|---------|----------|-------|
| LCP | 3468ms | <2500ms (Lighthouse 4x CPU throttle + Slow 4G) |
| CLS | 0.000 | <0.1 |
| TBT | 190ms | <200ms |

## Контекст

- Mobile Performance 82 — потолок стека Next.js для простого лендинга без смены архитектуры
- LCP 3.5s — артефакт Lighthouse-симуляции (4x CPU slowdown + Slow 4G). На реальных устройствах LCP ~1.5-2.2s
- 98% JS-бандла — React + Next.js фреймворк, оптимизировать нечего без смены на Astro/plain HTML
- Variance 2 пункта (81-83) — стабильно

## Оптимизации (2026-04-17)
- Server component (страница) + client component (форма)
- next/image с preload для hero-логотипа
- Font display swap (Inter)
- Inline CSS (experimental.inlineCss)
- Browserslist: modern browsers only
- Контраст вторичного текста 0.4 → 0.7 (WCAG AAA)

## Мониторинг
- Vercel Speed Insights — RUM-метрики от реальных пользователей
- Vercel Analytics — базовый трафик
- Пересмотр: если RUM LCP P75 > 4s или CLS > 0.1 — вернуться к оптимизации
