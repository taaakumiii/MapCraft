// MapCraft's compact Japanese menu. Renderer and terrain sampling: kirbycope/minimap-bedrock (MIT).
import { ActionFormData } from '@minecraft/server-ui';
import { save, settingsOf, step } from './settings.js';

export function openMenu(player) {
    const settings = settingsOf(player);
    const buttons = [
        ['拡大する（近くを見る）', () => step(player, 'zoom', -1)],
        [`縮小する（1マス ${settings.zoom} ブロック）`, () => step(player, 'zoom', 1)],
        [`地図を大きく（現在 ${settings.size} マス）`, () => step(player, 'size', 1)],
        ['地図を小さく', () => step(player, 'size', -1)],
        [`形：${settings.shape === 'square' ? '四角' : '丸'}`, () => save(player, {shape: settings.shape === 'square' ? 'circle' : 'square'})],
        [`地図の回転：${settings.rotate ? 'あり' : '北を上に固定'}`, () => save(player, {rotate: !settings.rotate})],
        [`プレイヤー・Mob：${settings.entities ? '表示' : '非表示'}`, () => save(player, {entities: !settings.entities})],
        [`緯度・経度：${settings.coords ? '表示' : '非表示'}`, () => save(player, {coords: !settings.coords})],
        [`方位・X/Z座標：${settings.angle ? '表示' : '非表示'}`, () => save(player, {angle: !settings.angle})],
        ['小さい表示に戻す', () => save(player, {on:true, size:16, zoom:2, enlarged:false, coords:true, angle:true, biome:false, light:false, time:false})],
        [settings.on ? 'ミニマップを隠す' : 'ミニマップを表示', () => save(player, {on:!settings.on})]
    ];
    const form = new ActionFormData().title('MapCraft ミニマップ').body('右上にゲーム内の地形を表示します。設定はプレイヤーごとに保存されます。');
    for (const [label] of buttons) form.button(label);
    return form.show(player).then((response) => {
        if (response.canceled || response.selection === undefined) return;
        buttons[response.selection][1]();
        if (response.selection !== buttons.length - 1) return openMenu(player);
    });
}
