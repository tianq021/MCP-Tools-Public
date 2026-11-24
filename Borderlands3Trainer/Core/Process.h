//
// Created by hp on 2026/9/18.
//

#ifndef CODEX_MMX_PROCESS_H
#define CODEX_MMX_PROCESS_H

#endif //CODEX_MMX_PROCESS_H
#pragma once

#include <Windows.h>
#include <string>

class Process
{
public:
    // 构造函数
    Process();

    // 析构函数
    // 对象销毁时自动释放进程句柄
    ~Process();

    // 禁止复制
    Process(const Process&) = delete;
    Process& operator=(const Process&) = delete;

    // 根据进程名连接游戏
    // 例如：L"Borderlands3.exe"
    bool Attach(const std::wstring& processName);

    // 主动断开连接
    void Detach();

    // 当前是否已经成功连接进程
    bool IsAttached() const;

    // 游戏进程是否仍然运行
    bool IsRunning() const;

    // 获取 PID
    DWORD GetProcessId() const;

    // 获取进程 Handle
    // 后面的 Memory 模块会使用
    HANDLE GetHandle() const;

private:
    // 根据进程名查找 PID
    DWORD FindProcessId(const std::wstring& processName) const;

    // 根据 PID 打开进程
    bool Open(DWORD processId);

private:
    // 当前游戏 PID
    DWORD processId_;

    // Windows 进程句柄
    HANDLE processHandle_;
};