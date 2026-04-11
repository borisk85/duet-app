import 'dart:async';
import 'package:flutter/material.dart';
import '../models/pairing_result.dart';
import '../services/api_service.dart';
import 'result_screen.dart';

class FavoritesScreen extends StatefulWidget {
  final VoidCallback? onGoHome;
  const FavoritesScreen({super.key, this.onGoHome});

  @override
  State<FavoritesScreen> createState() => _FavoritesScreenState();
}

class _FavoritesScreenState extends State<FavoritesScreen> {
  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);
  // Material Design red — стандартный цвет для destructive actions
  static const _deleteRed = Color(0xFFE53935);

  List<PairingResponse> _favorites = [];
  bool _loading = true;

  // Прогресс свайпа для каждой карточки (key Dismissible → 0.0..1.0).
  // Используется для анимации иконки корзины — scale растет по мере свайпа.
  // Источник данных: Dismissible.onUpdate(DismissUpdateDetails details) → details.progress
  final Map<Key, double> _swipeProgress = {};

  // OverlayEntry для undo. Используется вместо SnackBar потому что SnackBar
  // привязывается к root MaterialApp ScaffoldMessenger и не убирается при
  // переключении вкладок (зависает на всех экранах). Overlay с Timer чистый.
  OverlayEntry? _undoOverlay;
  Timer? _undoTimer;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _undoTimer?.cancel();
    _undoOverlay?.remove();
    _undoOverlay = null;
    super.dispose();
  }

  Future<void> _load() async {
    final data = await ApiService.getFavorites();
    setState(() {
      _favorites = data;
      _loading = false;
    });
  }

  void _remove(int index) {
    final removedItem = _favorites[index];
    final removedIndex = index;
    setState(() => _favorites.removeAt(index));
    _showUndoOverlay(removedItem, removedIndex);
  }

  void _hideUndoOverlay() {
    _undoTimer?.cancel();
    _undoTimer = null;
    _undoOverlay?.remove();
    _undoOverlay = null;
  }

  void _showUndoOverlay(PairingResponse removedItem, int removedIndex) {
    // Закрываем предыдущий overlay если он есть и фиксируем то удаление в БД
    if (_undoOverlay != null) {
      _hideUndoOverlay();
    }

    bool undone = false;
    final overlay = Overlay.of(context);

    // Overlay вставляется в root Overlay.of(context) — positioning идет
    // от физического низа экрана, не от body. bottom: 100 Boris не хватало
    // на Xiaomi — краями overlay залезал на навбар. Поднято до 130:
    // 60 (navbar) + 40 (gesture bar + буфер) + 30 (явный зазор чтобы не
    // касаться верхнего края меню). На устройствах без gesture bar выглядит
    // чуть выше чем нужно, но это лучше чем перекрытие.
    final entry = OverlayEntry(
      builder: (ctx) => Positioned(
        left: 20,
        right: 20,
        bottom: 130,
        child: Material(
          color: Colors.transparent,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(
              color: _card,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.white.withOpacity(0.1)),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withOpacity(0.4),
                  blurRadius: 12,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            child: Row(
              children: [
                const Expanded(
                  child: Text(
                    'Удалено',
                    style: TextStyle(color: Colors.white, fontWeight: FontWeight.w500, fontSize: 14),
                  ),
                ),
                GestureDetector(
                  onTap: () {
                    undone = true;
                    _hideUndoOverlay();
                    setState(() {
                      if (removedIndex <= _favorites.length) {
                        _favorites.insert(removedIndex, removedItem);
                      } else {
                        _favorites.add(removedItem);
                      }
                    });
                  },
                  child: const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    child: Text(
                      'Отменить',
                      style: TextStyle(color: _gold, fontWeight: FontWeight.w700, fontSize: 14),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );

    _undoOverlay = entry;
    overlay.insert(entry);

    // Жесткий таймер на 3 секунды — убирает overlay в любом случае.
    // Не зависит от ScaffoldMessenger, не зависит от вкладки, не зависит ни от чего.
    _undoTimer = Timer(const Duration(seconds: 3), () {
      _hideUndoOverlay();
      if (!undone && removedItem.id != null) {
        ApiService.removeFavorite(removedItem.id!);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _bg,
        surfaceTintColor: Colors.transparent,
        title: const Text(
          'Избранное',
          style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _gold))
          : _favorites.isEmpty
              ? _buildEmpty()
              : _buildList(),
    );
  }

  Widget _buildEmpty() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(40),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.star_rounded, color: _gold, size: 56),
            const SizedBox(height: 20),
            const Text(
              'Здесь будут ваши дуэты',
              style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 10),
            Text(
              'Найдите дуэт и сохраните его — он появится здесь',
              style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 14, height: 1.5),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 32),
            SizedBox(
              width: double.infinity,
              height: 50,
              child: ElevatedButton(
                onPressed: widget.onGoHome,
                style: ElevatedButton.styleFrom(
                  backgroundColor: _gold,
                  foregroundColor: _bg,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                  elevation: 0,
                ),
                child: const Text('Подобрать первый дуэт', style: TextStyle(fontWeight: FontWeight.w700)),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildList() {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      itemCount: _favorites.length,
      itemBuilder: (context, index) {
        final item = _favorites[index];
        return _buildCard(item, index);
      },
    );
  }

  String _detailLabel(String level) {
    switch (level) {
      case 'simple':
        return 'Просто';
      case 'expert':
        return 'Эксперт';
      case 'standard':
      default:
        return 'Стандарт';
    }
  }

  Widget _buildCard(PairingResponse item, int index) {
    final firstResult = item.results.isNotEmpty ? item.results.first : null;
    final cardKey = Key('fav_${item.dish}_${item.budget}_${item.createdAt.millisecondsSinceEpoch}');
    // 0.0 пока пользователь не свайпает, растет до 1.0 при полном свайпе
    final progress = _swipeProgress[cardKey] ?? 0.0;
    // Scale корзинки: от 0.7 в покое до 1.2 в полном свайпе. Линейная интерполяция.
    final iconScale = 0.7 + (progress.clamp(0.0, 1.0) * 0.5);
    return Dismissible(
      key: cardKey,
      direction: DismissDirection.endToStart,
      // Короткий resize чтобы не оставался "фантом" карточки после быстрого свайпа.
      // Дефолт 300мс давал серый шлейф цвета _card на фоне _bg. 150мс — чисто.
      resizeDuration: const Duration(milliseconds: 150),
      onUpdate: (details) {
        // Триггер только при заметном изменении — снижает количество ребилдов
        final newProgress = details.progress;
        final oldProgress = _swipeProgress[cardKey] ?? 0.0;
        if ((newProgress - oldProgress).abs() > 0.02) {
          setState(() => _swipeProgress[cardKey] = newProgress);
        }
      },
      background: Container(
        margin: const EdgeInsets.only(bottom: 12),
        decoration: BoxDecoration(
          // _bg (а не _card) — фон background при свайпе совпадает с фоном экрана.
          // Раньше был _card = такой же как у карточки → серый шлейф на 300мс.
          color: _bg,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Align(
          alignment: Alignment.centerRight,
          child: Container(
            width: 80,
            decoration: const BoxDecoration(
              color: _deleteRed,
              borderRadius: BorderRadius.only(
                topRight: Radius.circular(14),
                bottomRight: Radius.circular(14),
              ),
            ),
            child: Center(
              child: AnimatedScale(
                scale: iconScale,
                duration: const Duration(milliseconds: 80),
                curve: Curves.easeOut,
                child: const Icon(
                  Icons.delete_rounded,
                  color: Colors.white,
                  size: 24,
                ),
              ),
            ),
          ),
        ),
      ),
      onDismissed: (_) {
        _swipeProgress.remove(cardKey);
        _remove(index);
      },
      child: GestureDetector(
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => ResultScreen(response: item)),
        ),
        child: Container(
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: _card,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: Colors.white.withOpacity(0.06)),
          ),
          child: Row(
            children: [
              Text(
                firstResult?.resolvedEmoji ?? '🍷',
                style: const TextStyle(fontSize: 32),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.dish.isNotEmpty ? '${item.dish[0].toUpperCase()}${item.dish.substring(1)}' : '',
                      style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      firstResult != null ? '${firstResult.alcoholType} · ${firstResult.brand}' : '',
                      style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 13),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                    decoration: BoxDecoration(
                      color: _gold.withOpacity(0.12),
                      borderRadius: BorderRadius.circular(5),
                    ),
                    child: Text(
                      _detailLabel(item.detailLevel),
                      style: TextStyle(
                        color: _gold.withOpacity(0.85),
                        fontSize: 10,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.2,
                      ),
                    ),
                  ),
                  const SizedBox(height: 6),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                    decoration: BoxDecoration(
                      color: item.mode == 'food_to_alcohol'
                          ? _gold.withOpacity(0.12)
                          : Colors.blue.withOpacity(0.12),
                      borderRadius: BorderRadius.circular(5),
                    ),
                    child: Text(
                      item.mode == 'food_to_alcohol' ? 'Еда → Напиток' : 'Напиток → Еда',
                      style: TextStyle(
                        color: item.mode == 'food_to_alcohol'
                            ? _gold.withOpacity(0.85)
                            : Colors.lightBlue.shade200,
                        fontSize: 10,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.1,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
