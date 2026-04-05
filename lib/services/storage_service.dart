import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/pairing_result.dart';

class StorageService {
  static const _favoritesKey = 'favorites';
  static const _historyKey = 'history';
  static const _historyDays = 30;

  // ИЗБРАННОЕ

  static Future<List<PairingResponse>> getFavorites() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getStringList(_favoritesKey) ?? [];
    return raw.map((s) => PairingResponse.fromJson(jsonDecode(s))).toList();
  }

  static Future<void> saveToFavorites(PairingResponse response) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getStringList(_favoritesKey) ?? [];
    raw.insert(0, jsonEncode(response.toJson()));
    await prefs.setStringList(_favoritesKey, raw);
  }

  static Future<void> removeFromFavorites(int index) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getStringList(_favoritesKey) ?? [];
    if (index < raw.length) {
      raw.removeAt(index);
      await prefs.setStringList(_favoritesKey, raw);
    }
  }

  // ИСТОРИЯ

  static Future<List<PairingResponse>> getHistory() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getStringList(_historyKey) ?? [];
    final cutoff = DateTime.now().subtract(const Duration(days: _historyDays));
    final valid = raw.where((s) {
      try {
        final data = jsonDecode(s);
        final date = DateTime.parse(data['created_at'] ?? '');
        return date.isAfter(cutoff);
      } catch (_) {
        return false;
      }
    }).toList();
    // Если были удалены просроченные — сохраняем обновленный список
    if (valid.length != raw.length) {
      await prefs.setStringList(_historyKey, valid);
    }
    return valid.map((s) => PairingResponse.fromJson(jsonDecode(s))).toList();
  }

  static Future<void> saveToHistory(PairingResponse response) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getStringList(_historyKey) ?? [];
    raw.insert(0, jsonEncode(response.toJson()));
    // Не больше 200 записей в истории
    if (raw.length > 200) raw.removeLast();
    await prefs.setStringList(_historyKey, raw);
    // Очищаем записи старше 30 дней
    await getHistory();
  }
}
