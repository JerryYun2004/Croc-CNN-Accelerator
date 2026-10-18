croc_brew_prefix="$(brew --prefix)"
export PATH="$croc_brew_prefix/bin:$PATH"

for croc_tool in make coreutils gnu-sed grep gawk; do
    export PATH="$croc_brew_prefix/opt/$croc_tool/libexec/gnubin:$PATH"
done

export CROC_PDK=sg13cmos5l
export VERILATOR_JOBS=2
unset VERILATOR_ROOT

# Native Verilator LZ4 paths
croc_lz4_prefix="$(brew --prefix lz4)"
export USER_CPPFLAGS="-I$croc_lz4_prefix/include ${USER_CPPFLAGS:-}"
export USER_LDFLAGS="-L$croc_lz4_prefix/lib ${USER_LDFLAGS:-}"
