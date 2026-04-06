import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/auth_service.dart';
import '../services/api_service.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen>
    with SingleTickerProviderStateMixin {
  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  String _region = 'СНГ';
  Set<String> _preferredTypes = {};
  String _detailLevel = 'standard';
  bool _loading = true;
  bool _isPremium = false;
  int _pairingCount = 0;
  int _pairingLimit = 10;

  late final AnimationController _shakeController;
  late final Animation<double> _shakeAnimation;

  final _regions = ['СНГ', 'Россия', 'Казахстан', 'Украина', 'Беларусь'];
  final _alcoholTypes = [
    {'key': 'wine', 'label': 'Вино', 'emoji': '🍷'},
    {'key': 'whiskey', 'label': 'Виски', 'emoji': '🥃'},
    {'key': 'cognac', 'label': 'Коньяк', 'emoji': '🥃'},
    {'key': 'beer', 'label': 'Пиво', 'emoji': '🍺'},
    {'key': 'vodka', 'label': 'Водка', 'emoji': '🫗'},
    {'key': 'gin', 'label': 'Джин', 'emoji': '🌿'},
    {'key': 'rum', 'label': 'Ром', 'emoji': '🍹'},
    {'key': 'tequila', 'label': 'Текила', 'emoji': '🌵'},
    {'key': 'cocktails', 'label': 'Коктейли', 'emoji': '🍸'},
    {'key': 'sparkling', 'label': 'Игристое', 'emoji': '🥂'},
  ];

  @override
  void initState() {
    super.initState();
    _load();
    _shakeController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 400),
    );
    _shakeAnimation = TweenSequence<double>([
      TweenSequenceItem(tween: Tween(begin: 0, end: -6), weight: 1),
      TweenSequenceItem(tween: Tween(begin: -6, end: 6), weight: 2),
      TweenSequenceItem(tween: Tween(begin: 6, end: -6), weight: 2),
      TweenSequenceItem(tween: Tween(begin: -6, end: 6), weight: 2),
      TweenSequenceItem(tween: Tween(begin: 6, end: 0), weight: 1),
    ]).animate(_shakeController);
  }

  @override
  void dispose() {
    _shakeController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _region = prefs.getString('region') ?? 'СНГ';
      _preferredTypes = (prefs.getStringList('preferred_types') ?? []).toSet();
      _detailLevel = prefs.getString('detail_level') ?? 'standard';
      _loading = false;
    });
    // Загружаем данные профиля с сервера (не блокируем UI)
    final me = await ApiService.getMe();
    if (me != null && mounted) {
      setState(() {
        _isPremium = me['is_premium'] == true;
        _pairingCount = (me['pairing_count'] as num?)?.toInt() ?? 0;
        _pairingLimit = (me['pairing_limit'] as num?)?.toInt() ?? 10;
      });
    }
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('region', _region);
    await prefs.setStringList('preferred_types', _preferredTypes.toList());
    await prefs.setString('detail_level', _detailLevel);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _bg,
        surfaceTintColor: Colors.transparent,
        title: const Text(
          'Профиль',
          style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _gold))
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                _buildSection('Регион', _buildRegionSelector()),
                const SizedBox(height: 24),
                _buildSection('Детализация подборок', _buildDetailLevelSelector()),
                const SizedBox(height: 24),
                _buildSection(
                  'Предпочтения',
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Что предпочитаете пить? AI будет учитывать это в подборках.',
                        style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 13, height: 1.5),
                      ),
                      const SizedBox(height: 12),
                      _buildAlcoholGrid(),
                    ],
                  ),
                ),
                const SizedBox(height: 24),
                _buildSection('Подписка', _buildSubscription()),
                const SizedBox(height: 24),
                _buildSignOutButton(),
                const SizedBox(height: 32),
              ],
            ),
    );
  }

  Widget _buildSection(String title, Widget child) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title.toUpperCase(),
          style: TextStyle(
            color: Colors.white.withOpacity(0.35),
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 1,
          ),
        ),
        const SizedBox(height: 12),
        child,
      ],
    );
  }

  Widget _buildRegionSelector() {
    return Container(
      decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(12)),
      child: Column(
        children: _regions.map((r) {
          final selected = _region == r;
          return GestureDetector(
            onTap: () {
              setState(() => _region = r);
              _save();
            },
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              decoration: BoxDecoration(
                border: Border(bottom: BorderSide(color: Colors.white.withOpacity(0.05))),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Text(r, style: const TextStyle(color: Colors.white, fontSize: 15)),
                  ),
                  if (selected)
                    const Icon(Icons.check_rounded, color: _gold, size: 20),
                ],
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildDetailLevelSelector() {
    final levels = [
      {
        'key': 'simple',
        'label': 'Просто',
        'desc': 'Краткое объяснение для новичка',
        'icon': Icons.lightbulb_outline_rounded,
      },
      {
        'key': 'standard',
        'label': 'Стандарт',
        'desc': 'Объяснение почему сочетается + совет по подаче',
        'icon': Icons.tune_rounded,
      },
      {
        'key': 'expert',
        'label': 'Эксперт',
        'desc': 'Сорт, регион, выдержка, температура, бокал',
        'icon': Icons.workspace_premium_rounded,
      },
    ];
    return Container(
      decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(12)),
      child: Column(
        children: levels.asMap().entries.map((entry) {
          final isLast = entry.key == levels.length - 1;
          final level = entry.value;
          final key = level['key'] as String;
          final selected = _detailLevel == key;
          return GestureDetector(
            onTap: () {
              HapticFeedback.lightImpact();
              setState(() => _detailLevel = key);
              _save();
            },
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              decoration: BoxDecoration(
                border: isLast
                    ? null
                    : Border(bottom: BorderSide(color: Colors.white.withOpacity(0.05))),
              ),
              child: Row(
                children: [
                  Icon(
                    level['icon'] as IconData,
                    color: selected ? _gold : Colors.white.withOpacity(0.4),
                    size: 22,
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          level['label'] as String,
                          style: TextStyle(
                            color: selected ? _gold : Colors.white,
                            fontSize: 15,
                            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          level['desc'] as String,
                          style: TextStyle(
                            color: Colors.white.withOpacity(0.4),
                            fontSize: 12,
                            height: 1.3,
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (selected)
                    const Icon(Icons.check_rounded, color: _gold, size: 20),
                ],
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildAlcoholGrid() {
    final count = _preferredTypes.length;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: AnimatedSwitcher(
            duration: const Duration(milliseconds: 200),
            child: Align(
              alignment: Alignment.centerLeft,
              key: ValueKey(count),
              child: Text(
                count == 0
                    ? 'Выберите до 3 категорий'
                    : count == 3
                        ? '3 из 3 выбрано ✓'
                        : '$count из 3 выбрано',
                style: TextStyle(
                  color: count == 0 ? Colors.white70 : _gold,
                  fontSize: 13,
                  fontWeight: count > 0 ? FontWeight.w500 : FontWeight.w400,
                ),
              ),
            ),
          ),
        ),
        Wrap(
      spacing: 8,
      runSpacing: 8,
      children: _alcoholTypes.map((type) {
        final key = type['key']!;
        final selected = _preferredTypes.contains(key);
        return GestureDetector(
          onTap: () {
            if (!selected && _preferredTypes.length >= 3) {
              HapticFeedback.heavyImpact();
              _shakeController.forward(from: 0);
              return;
            }
            setState(() {
              if (selected) {
                _preferredTypes.remove(key);
              } else {
                _preferredTypes.add(key);
              }
            });
            _save();
          },
          child: AnimatedBuilder(
            animation: _shakeAnimation,
            builder: (_, child) => Transform.translate(
              offset: Offset(selected ? _shakeAnimation.value : 0, 0),
              child: child,
            ),
            child: AnimatedContainer(
            duration: const Duration(milliseconds: 200),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: selected ? _gold.withOpacity(0.15) : _card,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: selected ? _gold : Colors.white.withOpacity(0.08),
                width: selected ? 1.5 : 1,
              ),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(type['emoji']!, style: const TextStyle(fontSize: 16)),
                const SizedBox(width: 6),
                Text(
                  type['label']!,
                  style: TextStyle(
                    color: selected ? _gold : Colors.white.withOpacity(0.6),
                    fontSize: 13,
                    fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                  ),
                ),
              ],
            ),
          ),
            ),
        );
      }).toList(),
        ),
      ],
    );
  }

  Widget _buildSignOutButton() {
    final user = AuthService.currentUser;
    final isAnon = user?.isAnonymous ?? true;
    final label = isAnon ? 'Аноним' : (user?.email ?? '');

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (label.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(
              label,
              style: TextStyle(color: Colors.white.withOpacity(0.3), fontSize: 13),
            ),
          ),
        SizedBox(
          width: double.infinity,
          height: 48,
          child: OutlinedButton(
            onPressed: () async {
              await AuthService.signOut();
            },
            style: OutlinedButton.styleFrom(
              foregroundColor: Colors.red.shade400,
              side: BorderSide(color: Colors.red.shade900),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            ),
            child: const Text('Выйти из аккаунта'),
          ),
        ),
      ],
    );
  }

  Widget _buildSubscription() {
    final left = (_pairingLimit - _pairingCount).clamp(0, _pairingLimit);
    final progress = _pairingLimit > 0 ? (_pairingCount / _pairingLimit).clamp(0.0, 1.0) : 0.0;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: _gold.withOpacity(0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Text('⚡', style: TextStyle(fontSize: 20)),
              const SizedBox(width: 8),
              Text(
                _isPremium ? 'Premium' : 'Бесплатный план',
                style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600),
              ),
              const Spacer(),
              if (_isPremium)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: _gold.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: const Text('Безлимит', style: TextStyle(color: _gold, fontSize: 12, fontWeight: FontWeight.w600)),
                )
              else
                Text(
                  '$_pairingCount / $_pairingLimit',
                  style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 13),
                ),
            ],
          ),
          if (!_isPremium) ...[
            const SizedBox(height: 12),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: progress,
                backgroundColor: Colors.white.withOpacity(0.08),
                valueColor: AlwaysStoppedAnimation<Color>(
                  progress >= 1.0 ? Colors.red.shade700 : _gold,
                ),
                minHeight: 4,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              left > 0 ? 'Осталось $left подборок' : 'Лимит исчерпан — перейдите на Premium',
              style: TextStyle(
                color: left > 0 ? Colors.white.withOpacity(0.35) : Colors.red.shade400,
                fontSize: 12,
              ),
            ),
            const SizedBox(height: 14),
            SizedBox(
              width: double.infinity,
              height: 46,
              child: ElevatedButton(
                onPressed: () {},
                style: ElevatedButton.styleFrom(
                  backgroundColor: _gold,
                  foregroundColor: _bg,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  elevation: 0,
                ),
                child: const Text('Перейти на Premium', style: TextStyle(fontWeight: FontWeight.w700)),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
