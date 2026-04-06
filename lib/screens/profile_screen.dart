import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/auth_service.dart';

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
  bool _loading = true;

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
      _loading = false;
    });
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('region', _region);
    await prefs.setStringList('preferred_types', _preferredTypes.toList());
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
              const Text(
                'Бесплатный план',
                style: TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600),
              ),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.white.withOpacity(0.08),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text('10 подборок', style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 12)),
              ),
            ],
          ),
          const SizedBox(height: 12),
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
      ),
    );
  }
}
