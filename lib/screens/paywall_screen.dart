import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Paywall-экран — показывается когда пользователь упёрся в лимит 10 бесплатных подборок.
///
/// Триггер: ApiService.pairStream бросает PairingLimitException → ResultScreen
/// перехватывает → редиректит сюда вместо SnackBar с ошибкой.
///
/// Персонализация: показывает блюдо/напиток который пользователь хотел подобрать,
/// но не смог из-за лимита. Это превращает блокировку в момент максимальной мотивации
/// (психологически — конверсия выше когда блокировка совпадает с активным желанием).
///
/// Пока RevenueCat не подключён, кнопка "Перейти на Premium" даёт только haptic.
/// После интеграции RevenueCat — открывает purchase flow.
class PaywallScreen extends StatelessWidget {
  /// То что пользователь хотел подобрать (блюдо или напиток) — для персонализации
  final String dish;

  /// Режим в котором случилась блокировка ('food_to_alcohol' или 'alcohol_to_food')
  final String mode;

  const PaywallScreen({super.key, required this.dish, required this.mode});

  static const _gold = Color(0xFFC9A84C);
  // Более яркий золотой ТОЛЬКО для primary CTA "Перейти на Premium".
  // Основной _gold (#C9A84C) на тёмном фоне выглядит бежевым — для главного
  // акцента нужен насыщеннее. #E8B547 — на ~15% ярче, всё ещё в палитре,
  // не вульгарный жёлтый.
  static const _goldCta = Color(0xFFE8B547);
  static const _goldText = Color(0xFFD4B563);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  @override
  Widget build(BuildContext context) {
    final isFood = mode == 'food_to_alcohol';

    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Stack(
          children: [
            // Кнопка закрытия в правом верхнем углу
            Positioned(
              top: 8,
              right: 8,
              child: IconButton(
                icon: Icon(Icons.close_rounded, color: Colors.white.withOpacity(0.6), size: 28),
                onPressed: () => Navigator.of(context).pop(),
              ),
            ),
            Padding(
              // Нижний padding 60 — Xiaomi MIUI gesture exclusion zone у Boris
              // больше 40dp (с 40 кнопка "Может быть позже" вообще не тапалась).
              // 60 гарантированно поднимает TextButton выше любой системной
              // жестовой зоны на любом Android launcher.
              padding: const EdgeInsets.fromLTRB(24, 16, 24, 60),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const SizedBox(height: 24),
                  // Большая иконка
                  Center(
                    child: Container(
                      width: 88,
                      height: 88,
                      decoration: BoxDecoration(
                        color: _gold.withOpacity(0.12),
                        shape: BoxShape.circle,
                        border: Border.all(color: _gold.withOpacity(0.4), width: 2),
                      ),
                      child: const Center(
                        child: Text('🥂', style: TextStyle(fontSize: 40)),
                      ),
                    ),
                  ),
                  const SizedBox(height: 28),
                  // Заголовок
                  const Text(
                    'Бесплатный лимит исчерпан',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 24,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.3,
                    ),
                  ),
                  const SizedBox(height: 14),
                  // Персонализированный текст с блюдом
                  RichText(
                    textAlign: TextAlign.center,
                    text: TextSpan(
                      style: TextStyle(
                        color: Colors.white.withOpacity(0.65),
                        fontSize: 15,
                        height: 1.5,
                      ),
                      children: [
                        TextSpan(text: isFood ? 'Вы хотели подобрать напиток к ' : 'Вы хотели подобрать блюда к '),
                        TextSpan(
                          text: '«$dish»',
                          style: const TextStyle(color: _goldText, fontWeight: FontWeight.w600),
                        ),
                        const TextSpan(
                          text: ' — оформите Premium и получите результат прямо сейчас.',
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 32),
                  // Карточка преимуществ
                  Container(
                    padding: const EdgeInsets.all(20),
                    decoration: BoxDecoration(
                      color: _card,
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: _gold.withOpacity(0.3), width: 1),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: const [
                            Text('⚡', style: TextStyle(fontSize: 18)),
                            SizedBox(width: 8),
                            Text(
                              'Дуэт Premium',
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 16,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 16),
                        _buildBenefit('Безлимитные подборки напитков и блюд'),
                        _buildBenefit('Безлимитное избранное'),
                        _buildBenefit('История подборок 30 дней (вместо 7)'),
                        _buildBenefit('Экспертный режим с глубоким описанием'),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),
                  // Цена
                  Center(
                    child: Column(
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            const Text(
                              '\$9.99',
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 36,
                                fontWeight: FontWeight.w800,
                                letterSpacing: -0.5,
                                height: 1.0,
                              ),
                            ),
                            Padding(
                              padding: const EdgeInsets.only(bottom: 6, left: 4),
                              child: Text(
                                '/ год',
                                style: TextStyle(
                                  color: Colors.white.withOpacity(0.5),
                                  fontSize: 16,
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                            ),
                          ],
                        ),
                        Text(
                          'Меньше \$1 в месяц',
                          style: TextStyle(
                            color: Colors.white.withOpacity(0.4),
                            fontSize: 13,
                            height: 1.2,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const Spacer(),
                  // Цена должна "дышать" — пользователь успевает осознать
                  // $9.99 как дёшево прежде чем увидеть CTA-кнопку.
                  const SizedBox(height: 20),
                  // Кнопка покупки (только haptic — RevenueCat не интегрирован).
                  // backgroundColor _goldCta (#E8B547) ярче основного _gold —
                  // на тёмном фоне основной _gold выглядел бежевым, а CTA
                  // должен быть насыщенным акцентом.
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: ElevatedButton(
                      onPressed: () => HapticFeedback.mediumImpact(),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: _goldCta,
                        foregroundColor: _bg,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        elevation: 0,
                      ),
                      child: const Text(
                        'Перейти на Premium',
                        style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800, letterSpacing: 0.3),
                      ),
                    ),
                  ),
                  // Зазор между primary CTA и secondary action — чтобы
                  // "Может быть позже" не сливалась с золотой кнопкой.
                  const SizedBox(height: 16),
                  // Кнопка закрыть (вторичная) — увеличена с 14→15px и
                  // стала более читаемой, не теряется внизу экрана.
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    style: TextButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                    ),
                    child: Text(
                      'Может быть позже',
                      style: TextStyle(
                        color: Colors.white.withOpacity(0.55),
                        fontSize: 15,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBenefit(String text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.check_circle_rounded, color: _gold, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                color: Colors.white.withOpacity(0.85),
                fontSize: 14,
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }

}
